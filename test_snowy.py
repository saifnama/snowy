import pytest
import asyncio
from snowy import normalize_word, merge_words, is_valid_image_url, get_first_paragraph

def test_normalize_word():
    assert normalize_word("  India  ") == "india"
    assert normalize_word("Apple") == "apple"
    assert normalize_word("") == ""

def test_merge_words():
    words = ["India", "india", "USA", "usa", "Usa", "France"]
    merged = merge_words(words)
    assert len(merged) == 3
    assert merged["india"] == "India"  # Capitalized preferred
    assert merged["usa"] == "USA"    # Capitalized preferred
    assert merged["france"] == "France"

def test_is_valid_image_url():
    assert is_valid_image_url("https://example.com/image.jpg") == True
    assert is_valid_image_url("https://example.com/audio.ogg") == False
    assert is_valid_image_url("https://example.com/video.webm") == False
    assert is_valid_image_url("") == False

def test_get_first_paragraph():
    text = "First paragraph.\n\nSecond paragraph."
    assert get_first_paragraph(text) == "First paragraph."
    
    text_with_newline = "Sentence one.\nSentence two.\n\nNext para."
    # If the first part is short (<= 100), it returns the whole block as defined in snowy.py
    assert get_first_paragraph(text_with_newline) == "Sentence one.\nSentence two."
    
    long_sentence = "A" * 101 + ".\nNext line."
    assert get_first_paragraph(long_sentence) == "A" * 101 + "."
