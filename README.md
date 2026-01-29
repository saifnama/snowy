# Snowy

**Snowy** is a simple Python script that generates an HTML encyclopedia from a wordlist using Wikipedia/MediaWiki API.


<br>

<p align="center">
  <img src="assets/snowy.jpg" width="400" alt="picture of snowy being sleepy">
  <br>
  <em>picture of snowy being sleepy</em>
</p>

<br>

---

##  Getting Started

Follow these steps to set up and run `snowy` on your machine.

### 1. Clone the Repository
Open your terminal (or Command Prompt) and run:
```bash
git clone https://github.com/saifnama/snowy.git
cd snowy
```

### 2. Install Dependencies
Install the required libraries using the provided requirements file:
```bash
pip install -r requirements.txt
```

### 3. Generate Your Encyclopedia
Create a text file (e.g., `test_words.txt`) with one word per line, then run the script:
```bash
python snowy.py -i words.txt -o my_encyclopedia.html
```

---

## Performance & Safety

- **Async Fetching**: Snowy uses an asynchronous architecture (built on `asyncio`) to fetch data for multiple words in parallel. This makes it **3-4x faster**.
- **API Safety**: To respect Wikipedia's servers, Snowy uses a **Concurrency Limit** (max 3 simultaneous connections) and "Smart Retries" (exponential backoff). This ensures high performance without overwhelming the API or risking a block.

## Features

- **Summaries**: Fetches the first paragraph from Wikipedia for a concise overview.
- **Auto-Image Discovery**: Automatically finds the best high-quality image with accurate captions.
- **Linked Data**: Includes Wikidata IDs and direct links to Wikipedia for further reading.
- **Duplicate Merging**: Handles duplicate words automatically (e.g., "India" and "india" are merged).
- **Safe & Fast**: Built-in rate limiting (0.5s) to stay within Wikipedia's API guidelines.

---

## Requirements

- **Python 3.7+**

---

## License

This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for details.

---
