import json
import os
from datetime import datetime

def save_question_and_answer(username, question, answer, file_path='user.json'):
    # Ensure file exists with proper structure
    if not os.path.exists(file_path):
        data = {"users": []}
    else:
        with open(file_path, 'r') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {"users": []}

    # Find or create user entry
    user = next((u for u in data["users"] if u["username"] == username), None)
    if not user:
        user = {"username": username, "questions": []}
        data["users"].append(user)

    # Append new question/answer with timestamp
    user["questions"].append({
        "question": question,
        "answer": answer,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    # Save updated data
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2)