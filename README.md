cloud-ai-assistant/
├── .venv/
├── app.py                   ← Entry point (Streamlit UI)
├── prompts/
│   ├── __init__.py
│   └── base_prompt.txt
├── tagger_core/
│   ├── __init__.py
│   └── auto_tagger.py       ← logic copied from your AWS tagger
├── utils/
│   ├── __init__.py
│   └── aws_helpers.py       ← any shared helpers (e.g. IAM lookups)
├── README.md
├── requirements.txt
