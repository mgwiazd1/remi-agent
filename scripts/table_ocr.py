#!/usr/bin/env python3
"""
Table-aware OCR for PFT screenshots and other tabular data.
Uses Claude Haiku vision API to extract tables with high fidelity.
"""

import sys
import os
import json
import base64
from pathlib import Path
from typing import Optional
import anthropic

def encode_image(image_path: str) -> tuple[str, str]:
    """Encode image to base64 and detect media type."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")
    
    # Determine media type
    suffix = path.suffix.lower()
    media_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp'
    }
    
    media_type = media_types.get(suffix)
    if not media_type:
        raise ValueError(f"Unsupported image format: {suffix}")
    
    with open(image_path, 'rb') as f:
        image_data = base64.standard_b64encode(f.read()).decode('utf-8')
    
    return image_data, media_type

def extract_table_ocr(image_path: str, query: Optional[str] = None, output_format: str = "text") -> str:
    """
    Extract tables and structured data from image using Claude vision.
    
    Args:
        image_path: Path to image file
        query: Custom query (if None, uses default table extraction)
        output_format: 'text', 'json', or 'csv'
    
    Returns:
        Extracted table data as string
    """
    # Encode image
    image_data, media_type = encode_image(image_path)
    
    # Initialize Anthropic client
    client = anthropic.Anthropic(
        api_key=os.getenv('ANTHROPIC_API_KEY')
    )
    
    # Default prompt for table extraction
    if query is None:
        query = """Extract all tables from this image with perfect fidelity.

For each table:
1. Preserve the exact structure (rows and columns)
2. Extract all cell values precisely
3. Include headers and row labels
4. Handle merged cells appropriately
5. Note any special formatting (bold, colors, units)

If it's a medical/clinical report (like PFT - Pulmonary Function Test):
- Extract all measured values and their units
- Note normal/abnormal indicators
- List all test parameters and results
- Include reference ranges if shown
- Preserve all clinical notations

Output the extracted table in a clear, structured format that preserves the original layout."""
    
    # Call Claude API
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": query
                    }
                ],
            }
        ],
    )
    
    result = message.content[0].text
    
    # Format output
    if output_format == "json":
        # Try to parse as structured JSON
        return json.dumps({"extracted_data": result}, indent=2)
    elif output_format == "csv":
        # Return as-is (result should contain CSV-like format if requested)
        return result
    else:  # text
        return result

def main():
    if len(sys.argv) < 2:
        print("Usage: table_ocr.py <image_path> [query] [--format json|csv|text]")
        print()
        print("Examples:")
        print("  table_ocr.py pft_screenshot.png")
        print("  table_ocr.py pft_screenshot.png 'Extract FEV1, FVC, and FEV1/FVC ratio'")
        print("  table_ocr.py report.png --format json")
        sys.exit(1)
    
    image_path = sys.argv[1]
    
    # Parse arguments
    query = None
    output_format = "text"
    
    for i, arg in enumerate(sys.argv[2:], 2):
        if arg == "--format" and i + 1 < len(sys.argv):
            output_format = sys.argv[i + 1]
        elif not arg.startswith("--"):
            query = arg
    
    try:
        result = extract_table_ocr(image_path, query, output_format)
        print(result)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
