"""
task_router.py — Consuela Task Router
Routes work to the right model based on task type.
"""
import os
import json
import logging
import httpx
from typing import Optional
from enum import Enum

logger = logging.getLogger("consuela.router")

LOCAL_LABORER_URL = "http://127.0.0.1:8080/v1/chat/completions"
LOCAL_LABORER_HEALTH = "http://127.0.0.1:8080/health"
LOCAL_VISION_URL = "http://127.0.0.1:8081/v1/chat/completions"
LOCAL_VISION_HEALTH = "http://127.0.0.1:8081/health"
GLM5_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
GLM_API_KEY = os.environ.get("GLM_API_KEY", "")

class ModelTier(Enum):
    LOCAL_LABORER = "local_laborer"
    LOCAL_VISION = "local_vision"
    REMOTE_GLM5 = "remote_glm5"
    REMOTE_GLM47 = "remote_glm47"
    REMOTE_CLAUDE = "remote_claude"

LOCAL_LABORER_TASKS = {
    "book_chapter_extraction", "clinical_concept_extraction", "epub_chapter_extraction",
    "pdf_text_structuring", "junk_classification", "signal_preprocessing",
    "ticker_normalization", "direction_inference", "domain_detection",
    "vault_link_check", "frontmatter_normalization", "orphan_note_detection",
    "cross_reference_validation", "json_validation", "extraction_verification",
    "dedup_detection", "question_generation", "question_parsing",
    "log_analysis", "error_classification",
}

LOCAL_VISION_TASKS = {
    "chart_reading", "cxr_interpretation", "receipt_scanning", "document_ocr",
    "figure_description", "ecg_interpretation", "ventilator_waveform", "image_classification",
}

REMOTE_SYNTHESIS_TASKS = {
    "cross_theme_synthesis", "instinct_extraction", "morning_brief", "thesis_synthesis",
    "second_order_inference", "pattern_detection", "convergence_analysis",
    "velocity_report", "dossier_synthesis",
}

REMOTE_ROUTINE_TASKS = {
    "conversational_response", "simple_summary", "telegram_reply", "obsidian_note_formatting",
}

SPECIALIST_TASKS = {
    "cross_book_synthesis", "quality_review", "complex_clinical_reasoning", "deep_analysis",
}

def classify_task(task_type: str) -> ModelTier:
    if task_type in LOCAL_VISION_TASKS:
        return ModelTier.LOCAL_VISION
    elif task_type in LOCAL_LABORER_TASKS:
        return ModelTier.LOCAL_LABORER
    elif task_type in REMOTE_SYNTHESIS_TASKS:
        return ModelTier.REMOTE_GLM5
    elif task_type in REMOTE_ROUTINE_TASKS:
        return ModelTier.REMOTE_GLM47
    elif task_type in SPECIALIST_TASKS:
        return ModelTier.REMOTE_CLAUDE
    else:
        logger.warning(f"Unknown task type '{task_type}', defaulting to GLM-4.7")
        return ModelTier.REMOTE_GLM47

def _check_local_health(url: str, timeout: float = 2.0) -> bool:
    try:
        r = httpx.get(url, timeout=timeout)
        return r.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False

def is_laborer_available() -> bool:
    return _check_local_health(LOCAL_LABORER_HEALTH)

def is_vision_available() -> bool:
    return _check_local_health(LOCAL_VISION_HEALTH)

def _call_local(url, messages, max_tokens=2048, temperature=0.3, timeout=120.0):
    try:
        r = httpx.post(url, json={"model": "gemma", "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature}, timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Local call failed: {e}")
        return None

def _call_glm(messages, model="glm-5", max_tokens=2048, temperature=0.3, timeout=60.0):
    if not GLM_API_KEY:
        logger.error("GLM_API_KEY not set")
        return None
    try:
        r = httpx.post(GLM5_URL, headers={"Authorization": f"Bearer {GLM_API_KEY}",
            "Content-Type": "application/json"}, json={"model": model, "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature}, timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"GLM call failed: {e}")
        return None

def route_and_call(task_type, messages, max_tokens=2048, temperature=0.3, timeout=120.0, fallback=True):
    tier = classify_task(task_type)
    logger.info(f"Routing '{task_type}' -> {tier.value}")
    if tier == ModelTier.LOCAL_LABORER:
        if is_laborer_available():
            result = _call_local(LOCAL_LABORER_URL, messages, max_tokens, temperature, timeout)
            if result is not None:
                return result
        if fallback:
            return _call_glm(messages, model="glm-4.7", max_tokens=max_tokens, temperature=temperature, timeout=timeout)
        return None
    if tier == ModelTier.LOCAL_VISION:
        if is_vision_available():
            result = _call_local(LOCAL_VISION_URL, messages, max_tokens, temperature, timeout)
            if result is not None:
                return result
        return None
    if tier == ModelTier.REMOTE_GLM5:
        return _call_glm(messages, model="glm-5", max_tokens=max_tokens, temperature=temperature, timeout=timeout)
    if tier == ModelTier.REMOTE_GLM47:
        return _call_glm(messages, model="glm-4.7", max_tokens=max_tokens, temperature=temperature, timeout=timeout)
    if tier == ModelTier.REMOTE_CLAUDE:
        return _call_glm(messages, model="glm-5", max_tokens=max_tokens, temperature=temperature, timeout=timeout)
    return None

def extract(prompt, system="", max_tokens=2048):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return route_and_call("book_chapter_extraction", messages, max_tokens=max_tokens)

def classify(prompt, max_tokens=200):
    return route_and_call("junk_classification", [{"role": "user", "content": prompt}], max_tokens=max_tokens)

def synthesize(prompt, system="", max_tokens=2048):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return route_and_call("thesis_synthesis", messages, max_tokens=max_tokens)

def status():
    return {
        "laborer": {"available": is_laborer_available(), "url": LOCAL_LABORER_URL, "model": "gemma-4-26b-a4b"},
        "vision": {"available": is_vision_available(), "url": LOCAL_VISION_URL, "model": "gemma-4-e4b"},
        "glm5": {"available": bool(GLM_API_KEY), "model": "glm-5"},
    }

if __name__ == "__main__":
    import json as _json
    logging.basicConfig(level=logging.INFO)
    print(_json.dumps(status(), indent=2))
