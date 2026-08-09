import os
os.environ["QWEN_COMPACT_COMPANY_CONTEXT"] = "true"

from app.config import get_settings
get_settings.cache_clear()
settings = get_settings()
print(f"qwen_compact_company_context={settings.qwen_compact_company_context}")
