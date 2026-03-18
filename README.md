# AI Mock Interview Platform

FastAPI-based mock interview application that uses Mistral AI for answer evaluation, Whisper for (future) speech-to-text, pyttsx3 for text-to-speech questions, spaCy for resume parsing, and Redis for session state.

## Features

- Upload resume → auto difficulty detection
- Adaptive questions based on previous answers
- Communication + Technical scoring using Mistral
- Audio questions (TTS)
- Redis session persistence
- Spring Boot backend integration (for persistent storage)

## Tech Stack

- **Backend**: FastAPI, Uvicorn
- **AI**: Mistral API, OpenAI Whisper (planned), spaCy
- **Storage**: Redis
- **TTS**: pyttsx3
- **Document parsing**: pdfplumber, python-docx

## Prerequisites

- Python 3.10+
- Redis server running (localhost:6379 by default)
- Mistral API key
- (optional) Spring Boot backend running

## Installation

1. Clone the repository

```bash
git clone https://github.com/yourusername/ai-mock-interview.git
cd ai-mock-interview
