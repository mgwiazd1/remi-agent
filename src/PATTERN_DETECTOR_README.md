# Pattern Detector Module

## Overview

The `pattern_detector.py` module provides the `get_pattern_signal()` function for generating macro investment signals by analyzing theme convergence, velocity patterns, and second-order market implications.

## Function: `get_pattern_signal()`

### Signature
```python
def get_pattern_signal(db_path: str, lookback_days: int = 7) -> Dict[str, Any]
```

### Parameters
- **db_path** (str): Path to the SQLite database (e.g., `~/remi-intelligence/remi_intelligence.db`)
- **lookback_days** (int, default=7): Number of days to analyze for theme signals

### Returns
Dictionary with the following keys:

| Key | Type | Description |
|-----|------|-------------|
| `top_themes` | List[Dict] | Top 10 themes by velocity_score from the lookback period |
| `convergence` | List[Dict] | Themes appearing in 2+ distinct sources, sorted by source_count then velocity |
| `second_order` | List[Dict] | Second-order inferences for top 3 convergence themes |
| `regime_context` | Dict \| None | Current GLI stamp with macro regime state |
| `summary_text` | str | 3-4 sentence investor brief synthesizing key signals |
| `errors` | List[str] | Any errors encountered during data retrieval |

### Data Structures

#### Top Themes & Convergence Theme Object
```python
{
    "theme_label": str,           # Human-readable theme name
    "velocity_score": float,      # Momentum metric (0-100)
    "mention_count": int,         # Total mentions
    "source_count": int,          # Number of distinct sources
    "sources_list": str,          # Comma-separated source names
    "gli_phase_at_emergence": str # GLI phase when theme first appeared
}
```

#### Second-Order Inference Object
```python
{
    "trigger_theme": str,         # Initial theme that triggered inference
    "primary_impact": str,        # Direct market effect
    "second_order": str,          # Secondary cascading effect
    "third_order": str,           # Tertiary market implications
    "gli_regime_context": str     # Applicable GLI regime context
}
```

#### Regime Context Object (from GLI stamp)
```python
{
    "gli_phase": str,             # Current GLI phase (e.g., "turbulence")
    "gli_value_bn": float,        # Global liquidity value in billions
    "steno_regime": str,          # Steno-regime classification
    "fiscal_score": float,        # Fiscal capacity score (0-10)
    "transition_risk": float      # Risk of regime transition (0-10)
}
```

## Key Features

1. **No Exceptions**: The function never raises exceptions. All errors are captured in the `errors` list.

2. **Resilient**: Partial data is returned if sub-queries fail independently.

3. **Multi-Source Convergence Detection**: Identifies themes gaining traction across multiple independent sources, a key signal for macro turning points.

4. **Velocity-Based Ranking**: Uses exponential decay weighting to favor recent mentions from high-tier sources.

5. **LLM-Enhanced Synthesis**: Uses Claude Sonnet to generate concise, actionable investor briefs.

6. **Flexible Lookback**: Test different time windows with the `lookback_days` parameter.

## Usage Examples

### Basic Usage (7-day window)
```python
from pattern_detector import get_pattern_signal

result = get_pattern_signal("~/remi-intelligence/remi_intelligence.db")

# Access top themes
for theme in result['top_themes'][:5]:
    print(f"{theme['theme_label']}: velocity={theme['velocity_score']}")

# Get convergence signals
for conv in result['convergence']:
    print(f"CONVERGENCE: {conv['theme_label']} ({conv['source_count']} sources)")

# Read investor brief
print(result['summary_text'])
```

### Extended Analysis (30-day window)
```python
result = get_pattern_signal(
    "~/remi-intelligence/remi_intelligence.db",
    lookback_days=30
)
```

### Error Handling
```python
result = get_pattern_signal(db_path)

if result['errors']:
    print(f"⚠️ Encountered {len(result['errors'])} errors:")
    for error in result['errors']:
        print(f"  - {error}")
else:
    print("✓ All queries successful")
```

## Implementation Details

### Queries Performed

1. **Top Themes Query**
   - Selects top 10 themes by `velocity_score` from last N days
   - Fields: theme_label, velocity_score, mention_count, source_count, sources_list, gli_phase_at_emergence
   - Counts distinct sources per theme

2. **Convergence Query**
   - Finds themes with 2+ distinct sources
   - Sorts by source_count DESC, then velocity_score DESC
   - Same fields as top themes query

3. **Second-Order Inferences Query**
   - Retrieves inferences for top 3 convergence themes
   - Fields: trigger_theme, primary_impact, second_order, third_order, gli_regime_context

4. **GLI Stamp Fetch**
   - Calls `fetch_gli_stamp()` to get current macro regime context
   - Gracefully handles API unavailability

5. **Investor Brief Generation**
   - Uses Claude Sonnet 4.6 to synthesize 3-4 sentence plain-English brief
   - Incorporates top themes, convergence signals, and regime context
   - Fallback to rule-based summary if LLM unavailable

### Database Schema Requirements

Required tables:
- `themes`: theme_label, velocity_score, mention_count, gli_phase_at_emergence, last_seen_at
- `document_themes`: document_id, theme_id
- `documents`: source_name, ingested_at
- `second_order_inferences`: trigger_theme, primary_impact, second_order, third_order, gli_regime_context

## Performance

- **Typical runtime**: 5-15 seconds (depending on LLM availability)
- **Database query time**: <1 second
- **LLM brief generation**: 3-10 seconds

## Testing

Run the module directly to execute built-in tests:
```bash
cd ~/remi-intelligence/src
python3 pattern_detector.py
```

Or run the validation test suite:
```bash
python3 test_pattern_detector.py
```

Both tests verify:
- ✓ Function structure and return type
- ✓ All required fields present
- ✓ Data type validation
- ✓ Edge case handling
- ✓ No exceptions raised
- ✓ Graceful error handling

## Integration

The module is designed for integration into the REMI Intelligence pipeline:

```python
# In your workflow:
from pattern_detector import get_pattern_signal

# Get latest signals
signal = get_pattern_signal(db_path, lookback_days=7)

# Use in reports, dashboards, or further analysis
report = {
    "timestamp": datetime.utcnow().isoformat(),
    "signals": signal,
    "regime": signal['regime_context'],
    "brief": signal['summary_text']
}
```

## Troubleshooting

**No convergence themes found**
- May indicate recent market fragmentation or new trends
- Try increasing `lookback_days` to 14 or 30

**Empty summary_text**
- Occurs when no themes or convergence data exists
- Check database connectivity and ingestion status

**GLI stamp unavailable**
- The function still returns other data
- Check that AESTIMA service is accessible
- Regime context will be None but processing continues

**SQL errors in logs**
- The function gracefully captures these in the errors list
- Check database schema matches expected structure
- Verify database file exists and is readable

## Dependencies

- `anthropic>=0.25.0`: Claude LLM integration
- `sqlite3`: Standard library for database access
- `dotenv`: For environment variable loading
- Local: `gli_stamper.py` module

## Environment Variables

Required in `~/.env`:
- `ANTHROPIC_API_KEY`: API key for Claude access
- `DB_PATH` (optional): Default database path
- `AESTIMA_BASE_URL` (optional): GLI service endpoint

## Author Notes

- Designed to never raise exceptions; all errors are captured
- Suitable for production automation with no exception handling
- Can be run repeatedly without state side effects
- Returns partial data gracefully if any component fails
