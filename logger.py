import csv
from datetime import datetime

LOG_CSV = "log_puppie_conversation.csv"

def log_to_csv(speaker, text):
    """CSVに会話ログを書き出す"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_CSV, mode="a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([timestamp, speaker, text])