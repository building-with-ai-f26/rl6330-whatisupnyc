"""Compare AI sentiment ratings with the builder's labels in tests/sentiment_examples.json."""
import json
import os
import sys

import httpx
from dotenv import load_dotenv

from app import db
from app.ai import AIClient
from app.sentiment import Opinion, classify_opinions

load_dotenv()

with open("tests/sentiment_examples.json") as f:
    examples = [e for e in json.load(f) if e["label"]]
if not examples:
    sys.exit("No labels yet. Fill in each example's label: positive, mixed, negative, or unknown.")

conn = db.connect("whatisupnyc.db")
ai = AIClient(httpx.Client(), os.environ["OPENROUTER_API_KEY"], os.environ["OPENROUTER_MODEL"], conn)
correct = 0
for example in examples:
    rating = classify_opinions([Opinion(example["url"], example["text"])], ai)
    correct += rating == example["label"]
    if rating != example["label"]:
        print(f"Disagree: you said {example['label']}, AI said {rating}: {example['url']}")
print(f"Accuracy: {correct}/{len(examples)}")
