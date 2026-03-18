import logging 
import os 
import json 
import pyttsx3 
import redis 
import spacy 
import re 
import html 
import wave 
import shutil 
import tempfile 
import random 
import time 
import whisper 
import ast 
from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Request, Depends, status 
from fastapi.responses import JSONResponse 
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials 
from datetime import timedelta 
import pdfplumber 
from docx import Document 
import requests 
from pydantic import BaseModel 
from urllib.parse import urlparse, quote 
from collections import defaultdict 
 
# --- Ultra-Strict Sanitization Functions --- 
def ultra_clean_text(text: str) -> str: 
    """Remove ALL formatting and special characters from text""" 
    if not text or not isinstance(text, str): 
        return "" 
    text = re.sub(r'[\*\_\~\`\#\|\-\=\+\[\]\{\}\(\)\<\>]', '', text) 
    text = re.sub(r'\*\*.*?\*\*', '', text) 
    text = re.sub(r'\*.*?\*', '', text) 
    text = re.sub(r'\_.*?\_', '', text) 
    text = re.sub(r'\`.*?\`', '', text) 
    text = re.sub(r'\{\{.*?\}\}', '', text) 
    text = re.sub(r'\[.*?\]\(.*?\)', '', text) 
    text = re.sub(r'\!\[.*?\]\(.*?\)', '', text) 
    text = re.sub(r'\<.*?\>', '', text) 
    text = re.sub(r'\\+', '', text) 
    text = re.sub(r'\/\/', '', text) 
    text = re.sub(r'\/', '', text) 
    text = re.sub(r'\s+', ' ', text) 
    text = re.sub(r'[^\w\s\.\,\!\?\-\:\;]', '', text) 
    text = text.replace('\n', ' ').replace('\r', ' ') 
    text = ' '.join(text.split()) 
    return text.strip() 
 
def sanitize_for_output(text: str) -> str: 
    """Final sanitization for all user-facing output""" 
    if not text or not isinstance(text, str): 
        return "The response could not be processed." 
    text = ultra_clean_text(text) 
    if text: 
        if len(text) > 0: 
            text = text[0].upper() + text[1:] if len(text) > 1 else text.upper() 
        if not text.endswith(('.', '!', '?', ';', ':')): 
            text += '.' 
        text = text.replace('*', '').replace('_', '').replace('`', '') 
        text = text.replace('{', '').replace('}', '').replace('[', '').replace(']', '') 
        text = text.replace('(', '').replace(')', '') 
        text = ' '.join(text.split()) 
    return text 
 
def clean_feedback_text(text: str) -> str: 
    """Special cleaning for feedback text to remove all formatting""" 
    if not text or not isinstance(text, str): 
        return "Your performance has been evaluated based on your responses." 
    text = ultra_clean_text(text) 
    text = re.sub(r'^\s*(Here\'?s|Feedback on|Strengths|Areas to refine|To improve|Feedback)\s*[:\-\—]*\s*', '', text, flags=re.IGNORECASE) 
    text = re.sub(r'^\s*[\*\-\•]\s+', '', text, flags=re.MULTILINE) 
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE) 
    text = re.sub(r'["\'\«\»„“”]', '', text) 
    text = re.sub(r'\n+', ' ', text) 
    text = re.sub(r'\s+', ' ', text) 
    sentences = re.split(r'(?<=[\.!\?])\s+', text) 
    cleaned_sentences = [] 
    for sentence in sentences: 
        if sentence: 
            sentence = sentence[0].upper() + sentence[1:] if len(sentence) > 1 else sentence.upper() 
            if not sentence.endswith(('.', '!', '?')): 
                sentence += '.' 
            cleaned_sentences.append(sentence) 
    return ' '.join(cleaned_sentences) 
 
def sanitize_model_text(text: str) -> str: 
    """Remove code fences, inline code markers, markdown bold/italic, backslashes, and collapse whitespace.""" 
    if not text: 
        return "" 
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL) 
    text = re.sub(r'`([^`]*)`', r'\1', text) 
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text) 
    text = re.sub(r'\*(.*?)\*', r'\1', text) 
    text = text.replace('_', '') 
    text = text.replace('~', '') 
    text = text.replace('\\', '') 
    text = re.sub(r'\s+', ' ', text).strip() 
    return text 
 
import math 
 
def robust_json_load(raw: str) -> dict: 
    """ 
    Try to extract and normalize a JSON object returned by a language model. 
    Returns a Python dict or raises ValueError. 
    This function: 
     - removes backticks and leading 'json' tokens 
     - extracts the first {...} block 
     - converts single quotes to double quotes safely 
     - removes trailing commas 
     - quotes unquoted keys (best-effort) 
     - attempts json.loads 
    """ 
    if not raw or not isinstance(raw, str): 
        raise ValueError("Empty raw string") 
 
    s = raw.strip() 
 
    # Remove triple/back ticks and leading token like "json" 
    s = s.strip('`').strip() 
    if s.lower().startswith("json"): 
        s = s[4:].strip() 
 
    # Find the first and last brace pair to extract a JSON-like substring 
    start = s.find('{') 
    end = s.rfind('}') 
    if start == -1 or end == -1 or end <= start: 
        raise ValueError("No JSON object found in model output") 
 
    candidate = s[start:end+1] 
 
    # Replace any Windows newlines and collapse multiple newlines 
    candidate = candidate.replace('\r\n', '\n').replace('\r', '\n') 
 
    # Remove trailing commas before } or ] 
    candidate = re.sub(r',\s*(?=[}\]])', '', candidate) 
 
    # Replace single quotes with double quotes, but avoid inner contractions 
    # A simple heuristic: if single quotes enclose a JSON token, convert them. 
    candidate = re.sub(r"(?<!\\)'([^']*?)'(?!:)", r'"\1"', candidate) 
 
    # Quote unquoted keys: { key: value, ... } -> { "key": value, ... } 
    candidate = re.sub(r'(?P<brace>[\{\s,])\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', candidate) 
 
    # Remove control characters that break json 
    candidate = ''.join(ch for ch in candidate if (ch == '\n' or ch == '\t' or ord(ch) >= 32)) 
 
    # Final safety: collapse multiple spaces 
    candidate = re.sub(r'\s+', ' ', candidate) 
 
    # Try to load 
    try: 
        parsed = json.loads(candidate) 
        return parsed 
    except json.JSONDecodeError as e: 
        # Give a helpful error with snippets 
        raise ValueError(f"Failed to parse JSON from model output: {e}. Candidate start: {candidate[:200]}") 
 

# --- App and Logging --- 
app = FastAPI() 
logging.basicConfig( 
    level=logging.DEBUG, 
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s', 
    handlers=[logging.StreamHandler()] 
) 
logger = logging.getLogger(__name__) 
logger.info("Starting Mock Interview Application") 
 
# --- Authentication --- 
security = HTTPBearer() 
def get_token(credentials: HTTPAuthorizationCredentials = Depends(security)): 
    return credentials.credentials 
 
# --- Configurable Constants --- 
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "m7Y1IcXb9ujFjmt97NVGQk9ZnrmbKA7h") 
BASE_URL = os.getenv("BASE_URL", "http://localhost:8080") 
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions" 
DIFFICULTY_THRESHOLDS = { 
    "fresher": (0, 2), 
    "intermediate": (2, 5), 
    "experienced": (5, 7), 
    "senior": (7, 9), 
    "expert": (9, float('inf')) 
} 
DIFFICULTY_SCORE_RANGES = { 
    "fresher": (0, 50), 
    "intermediate": (50, 70), 
    "experienced": (70, 85), 
    "senior": (85, 95), 
    "expert": (95, 100) 
} 
SUPPORTED_AUDIO_EXTENSIONS = [ 
    ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", 
    ".mp4", ".mpeg", ".mpga", ".webm" 
]   
 
# --- Initialize Clients --- 
try: 
    logger.info("Loading Whisper Large V2 model...") 
    whisper_model = whisper.load_model("large-v2") 
    logger.info("Whisper Large V2 model loaded successfully") 
except Exception as e: 
    logger.exception("Failed to load Whisper model: %s", e) 
    raise 
 
try: 
    logger.info("Initializing Redis client...") 
    redis_client = redis.StrictRedis( 
        host=os.getenv("REDIS_HOST", "localhost"), 
        port=int(os.getenv("REDIS_PORT", 6379)), 
        db=int(os.getenv("REDIS_DB", 0)), 
        decode_responses=True 
    ) 
    logger.info("Redis client initialized successfully") 
    redis_client.ping() 
    logger.info("Redis connection test successful") 
except Exception as e: 
    logger.exception("Failed to initialize Redis client: %s", e) 
    raise 
 
try: 
    logger.info("Loading spaCy NLP model...") 
    nlp = spacy.load("en_core_web_sm") 
    logger.info("spaCy NLP model loaded successfully") 
except Exception as e: 
    logger.exception("Failed to load spaCy NLP model: %s", e) 
    raise 
 
# --- Request Models --- 
class InterviewSubmissionRequest(BaseModel): 
    user_id: str 
    aimock_id: str 
    question: str 
    answer: str 
    finalize: bool = False 
 
# --- Helper Functions --- 
def _mistral_headers(): 
    return { 
        "Authorization": f"Bearer {MISTRAL_API_KEY}", 
        "Content-Type": "application/json" 
    } 
 
def call_mistral_api(prompt: str, temperature: float = 0.5, max_tokens: int = 500, max_retries: int = 3) -> str: 
    logger.info("Calling Mistral API with prompt (first 200 chars): %s", 
                ultra_clean_text(prompt[:200]) if prompt else "") 
    if not prompt or not isinstance(prompt, str): 
        raise HTTPException(status_code=400, detail="Invalid prompt provided") 
 
    data = { 
        "model": "mistral-medium-latest", 
        "messages": [{"role": "user", "content": prompt}], 
        "temperature": temperature, 
        "top_p": 0.7, 
        "max_tokens": max_tokens 
    } 
    for attempt in range(max_retries): 
        try: 
            headers = _mistral_headers() 
            logger.debug("Sending request to Mistral API (attempt %d)", attempt + 1) 
            resp = requests.post(MISTRAL_API_URL, headers=headers, json=data, timeout=30) 
            if resp.status_code >= 400: 
                if resp.status_code == 429 and attempt < max_retries - 1: 
                    wait_time = 2 ** attempt 
                    logger.warning("Rate limited. Retrying in %s seconds...", wait_time) 
                    time.sleep(wait_time) 
                    continue 
                logger.error("Mistral API error %d: %s", resp.status_code, resp.text) 
                raise HTTPException(status_code=resp.status_code, detail=f"Mistral API error: {resp.text}") 
            body = resp.json() 
            # read raw content path used by Mistral chat completions 
            answer = body.get('choices', [{}])[0].get('message', {}).get('content', '') 
            if not answer: 
                raise HTTPException(status_code=500, detail="Mistral API returned empty response") 
            logger.info("Received raw response from Mistral (first 200 chars): %s", answer[:200]) 
            return answer 
        except requests.exceptions.RequestException as e: 
            logger.error("Mistral API request error: %s", e) 
            if attempt == max_retries - 1: 
                raise HTTPException(status_code=500, detail=f"Mistral API request failed: {str(e)}") 
            wait_time = 2 ** attempt 
            logger.warning("Request failed. Retrying in %s seconds...", wait_time) 
            time.sleep(wait_time) 
 
def analyze_answer_with_mistral(question_text: str, answer_text: str) -> dict: 
    """ 
    Analyze the given user answer using Mistral API for communication and technical quality. 
    Returns a dictionary containing structured feedback, scores, and follow-up suggestions. 
    """ 
 
    logger.info("Starting answer analysis with Mistral") 
    logger.debug("Question: %s", question_text[:500]) 
    logger.debug("Answer: %s", answer_text[:500]) 
 
    # Ensure inputs are valid 
    if not question_text or not answer_text: 
        raise HTTPException(status_code=400, detail="Question and answer text are required") 
 
    # Build the evaluation prompt 
    prompt = f""" 
    You are an expert technical interviewer. Analyze the following answer for both communication 
    and technical quality. 
 
    Question: 
    {question_text} 
 
    Candidate's Answer: 
    {answer_text} 
 
    Return STRICTLY VALID JSON (no markdown, no backticks, no explanations). 
    Use this exact structure and field names: 
 
    {{ 
      "communication_evaluation": {{ 
        "clarity": <number 1-5>, 
        "structure": <number 1-5>, 
        "relevance": <number 1-5>, 
        "depth": <number 1-5>, 
        "comments": ["short feedback sentences"] 
      }}, 
      "technical_evaluation": {{ 
        "accuracy": <number 1-5>, 
        "depth": <number 1-5>, 
        "relevance": <number 1-5>, 
        "efficiency": <number 1-5>, 
        "comments": ["short feedback sentences"] 
      }}, 
      "scores": {{ 
        "communication_score": <0.0-1.0>, 
        "technical_score": <0.0-1.0>, 
        "overall_score": <0.0-1.0> 
      }}, 
      "key_points": ["bullet points of strong areas"], 
      "suggested_followups": ["recommended next interview question topics"], 
      "difficulty_recommendation": "beginner/intermediate/advanced", 
      "difficulty_reasoning": "one-sentence reasoning" 
    }} 
 
    Return only the JSON object — no additional commentary. 
    """ 
 
    try: 
        raw = call_mistral_api(prompt, temperature=0.4, max_tokens=900) 
        if not raw: 
            raise HTTPException(status_code=500, detail="Empty response from Mistral") 
 
        logger.debug("Raw Mistral response (first 1000 chars): %s", raw[:1000]) 
 
        # --- Robust parsing --- 
        try: 
            parsed = robust_json_load(raw) 
        except ValueError as e: 
            logger.error("robust_json_load failed: %s", e) 
            json_match = re.search(r'\{(?:[^{}]|\{[^{}]*\})*\}', raw, re.DOTALL) 
            if json_match: 
                try: 
                    parsed = json.loads(json_match.group(0)) 
                except Exception as e2: 
                    logger.error("Fallback JSON parse failed: %s", e2) 
                    raise HTTPException(status_code=500, detail="Failed to parse Mistral response") 
            else: 
                raise HTTPException(status_code=500, detail="No valid JSON found in Mistral response") 
 
        # --- Key normalization helpers --- 
        def pick_key(d, *variants, default=None): 
            for v in variants: 
                if v in d: 
                    return d[v] 
            return default 
 
        comm_eval = pick_key(parsed, 
                             "communication_evaluation", "communicationevaluation", "communicationEvaluation", {}) 
        tech_eval = pick_key(parsed, 
                             "technical_evaluation", "technicalevaluation", "technicalEvaluation", {}) 
        scores = pick_key(parsed, "scores", "score", {}) 
 
        # --- Score normalization --- 
        def clamp_score(value, min_v=0.0, max_v=1.0): 
            try: 
                v = float(value) 
            except Exception: 
                return min_v 
            if v > 1.1 and v <= 5.0: 
                return max(min_v, min(max_v, (v - 1.0) / 4.0))  # map 1-5 to 0-1 
            if v > 5.0: 
                return max(min_v, min(max_v, v / 100.0))        # map 0-100 to 0-1 
            return max(min_v, min(max_v, v)) 
 
        communication_score = clamp_score(pick_key(scores, "communication_score", "communication", 0.0)) 
        technical_score = clamp_score(pick_key(scores, "technical_score", "technical", 0.0)) 
        overall_score = clamp_score( 
            pick_key(scores, "overall_score", "overall", (communication_score + technical_score) / 2.0) 
        ) 
 
        # --- Extract feedback comments --- 
        def extract_comments(block): 
            c = block.get("comments", "") 
            if isinstance(c, list): 
                return " ".join(str(x) for x in c[:3]) 
            return str(c) 
 
        comm_comments = extract_comments(comm_eval) 
        tech_comments = extract_comments(tech_eval) 
 
        # --- Extract key points & follow-ups --- 
        key_points = parsed.get("key_points") or parsed.get("keypoints") or parsed.get("keyPoints") or [] 
        suggested_followups = ( 
            parsed.get("suggested_followups") 
            or parsed.get("suggestedFollowups") 
            or parsed.get("followups") 
            or [] 
        ) 
 
        # Normalize to string lists 
        key_points = [clean_text_summary(str(k)) for k in (key_points if isinstance(key_points, (list, tuple)) else [key_points])] 
        suggested_followups = [clean_text_summary(str(s)) for s in (suggested_followups if isinstance(suggested_followups, (list, tuple)) else [suggested_followups])] 
 
        difficulty_recommendation = parsed.get("difficulty_recommendation", parsed.get("difficulty", "intermediate")) 
        difficulty_reasoning = clean_text_summary(parsed.get("difficulty_reasoning", "")) 
 
        # --- Build structured result --- 
        result = { 
            "communication_text": clean_text_summary(comm_comments), 
            "technical_text": clean_text_summary(tech_comments), 
            "communication_score": float(communication_score), 
            "technical_score": float(technical_score), 
            "key_points": key_points, 
            "suggested_followups": suggested_followups, 
            "difficulty_recommendation": difficulty_recommendation, 
            "difficulty_reasoning": difficulty_reasoning 
        } 
 
        logger.info("Mistral analysis completed successfully.") 
        return result 
 
    except HTTPException: 
        raise 
    except Exception as e: 
        logger.error("Error during answer analysis: %s", str(e), exc_info=True) 
        raise HTTPException(status_code=500, detail=f"Error analyzing answer with Mistral: {str(e)}") 
     
def evaluate_answer(question: str, answer: str, question_number: int) -> dict: 
    """ 
    Evaluates a candidate's answer based on the question and response. 
    It calls analyze_answer_with_mistral to get detailed insights. 
    Returns a dictionary with the score and feedback text. 
    """ 
    try: 
        analysis = analyze_answer_with_mistral(question, answer) 
        score = (analysis.get("communication_score", 0) + analysis.get("technical_score", 0)) / 2 
        feedback = ( 
            f"Communication: {analysis.get('communication_text', 'N/A')}. " 
            f"Technical: {analysis.get('technical_text', 'N/A')}." 
        ) 
        return { 
            "score": round(score, 2), 
            "feedback": feedback 
        } 
    except Exception as e: 
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}") 
 
def clean_text_summary(text: str) -> str: 
    if not text: 
        return "" 
    text = re.sub(r'[`{}\[\]]', '', str(text)) 
    text = re.sub(r'\s+', ' ', text).strip() 
    return text 
 
def map_level_to_score(level: str) -> float: 
    logger.debug("Mapping level to score: %s", level) 
    mapping = { 
        "fresher": 0.2, 
        "intermediate": 0.4, 
        "experienced": 0.6, 
        "senior": 0.8, 
        "expert": 1.0 
    } 
    score = mapping.get(level.lower(), 0.5) 
    logger.debug("Mapped level %s to score: %s", level, score) 
    return score 
 

def map_score_to_level(score: float) -> str: 
    logger.debug("Mapping score to level: %s", score) 
    if score < 0.3: 
        level = "fresher" 
    elif score < 0.5: 
        level = "intermediate" 
    elif score < 0.7: 
        level = "experienced" 
    elif score < 0.85: 
        level = "senior" 
    else: 
        level = "expert" 
    logger.debug("Mapped score %s to level: %s", score, level) 
    return level 
 

def determine_difficulty_level(experience_years: int) -> str: 
    logger.info("Determining difficulty level for experience years: %s", experience_years) 
    if 0 <= experience_years <= 2: 
        level = "fresher" 
    elif 2 < experience_years <= 5: 
        level = "intermediate" 
    elif 5 < experience_years <= 7: 
        level = "experienced" 
    elif 7 < experience_years <= 10: 
        level = "senior" 
    else: 
        level = "expert" 
    logger.info("Determined difficulty level: %s", level) 
    return level 
 

def generate_feedback(analysis: str, score: float, is_technical: bool = False) -> str: 
    field = "technical" if is_technical else "communication" 
    try: 
        clean_analysis = clean_feedback_text(analysis) 
        prompt = f""" 
        Generate professional feedback for a candidate's {field} skills based on this analysis: {clean_analysis} 
        Strict Requirements: 
        1. Use simple clear sentences only 
        2. Structure as 2-3 strengths followed by 1-2 areas for improvement 
        3. Keep to 3-4 concise sentences total 
        4. Score reference: {int(score)}/100 
        5. ABSOLUTELY NO FORMATTING SYMBOLS ALLOWED 
        6. Use proper sentence structure with periods only 
        Example format: 
        Your responses demonstrated clear understanding of core concepts. The examples provided were relevant and well-explained. To improve consider adding more specific metrics when discussing achievements. 
        """ 
        response = call_mistral_api(prompt, temperature=0.3, max_tokens=1024) 
        if not response: 
            if is_technical: 
                return "Your technical responses showed good understanding of the concepts. The examples provided were relevant. To improve consider adding more specific implementation details." 
            else: 
                return "Your communication was clear and structured. The responses were well-organized. To improve consider adding more specific examples." 
        cleaned = clean_feedback_text(response) 
        cleaned = re.sub(r'[\*\_\~\`\#\|\-\[\]\{\}\(\)\<\>\\\/]', '', cleaned) 
        cleaned = cleaned.replace('\n', ' ').replace('\r', ' ') 
        cleaned = ' '.join(cleaned.split()) 
        sentences = re.split(r'(?<=[\.!\?])\s+', cleaned) 
        final_sentences = [] 
        for sentence in sentences: 
            if sentence.strip(): 
                sentence = sentence[0].upper() + sentence[1:] if len(sentence) > 1 else sentence.upper() 
                if not sentence.endswith(('.', '!', '?')): 
                    sentence += '.' 
                final_sentences.append(sentence) 
        result = ' '.join(final_sentences) 
        result = re.sub(r'\.\s*\.', '.', result) 
        return result if result.endswith('.') else result + '.' 
    except Exception as e: 
        logger.error("Error generating feedback: %s", e) 
        if is_technical: 
            return "Your technical responses showed good understanding. To improve, consider adding more specific examples." 
        else: 
            return "Your communication was clear. To improve, consider providing more structured responses." 
 
def generate_interview_question( 
    role, job_description, skills, experience, education, 
    question_number, difficulty, previous_answers=None 
): 
    logger.info(f"Generating question #{question_number} with difficulty {difficulty}") 
 
    # First question remains static 
    if question_number == 0: 
        return "Tell us about your background, experience, and how it relates to this role." 
 
    # Context building (70% details) 
    skills_context = f"Candidate skills: {', '.join([sanitize_for_output(s) for s in skills[:5]])}" if skills else "No specific skills identified" 
    experience_context = f"Candidate experience: {sanitize_for_output(experience[0][:100])}" if experience else "" 
    education_context = f"Candidate education: {sanitize_for_output(education[0][:100])}" if education else "" 
 
    # Previous answers (30%) 
    answer_context = "" 
    if previous_answers and len(previous_answers) > 0: 
        last_answers = previous_answers[-min(2, len(previous_answers)):] 
        answer_context = "\nPrevious answers summary:\n" + "\n".join([ 
            f"Q: {sanitize_for_output(qa['question'])}\nA: {sanitize_for_output(qa['answer'][:150])}..." 
            for qa in last_answers 
        ]) 
 
    # Determine focus area 
    if question_number % 3 == 1: 
        focus_area = "technical problem-solving" 
    elif question_number % 3 == 2: 
        focus_area = "teamwork and behavioral skills" 
    else: 
        focus_area = "role-specific experience and challenges" 
 
    # Build prompt for Mistral 
    prompt = f""" 
    You are an expert interviewer designing adaptive interview questions. 
    Create exactly ONE new question for a {role} position. 
 
    Context weight: 
    - 70% based on the candidate's background (skills, education, experience) 
    - 30% based on their most recent answers. 
 
    Current difficulty level: {difficulty} 
    Focus area: {focus_area} 
 
    Candidate details: 
    {skills_context} 
    {experience_context} 
    {education_context} 
 
    {answer_context} 
 
    Job Description (summary): {sanitize_for_output(job_description[:200])} 
 
    Requirements: 
    1. The question MUST suit a {difficulty}-level candidate. 
    2. It should directly relate to {focus_area}. 
    3. Reference specific skills or context naturally. 
    4. Must be under 40 words. 
    5. Must end with a question mark. 
    6. Return ONLY the question text. 
    """ 
 
    try: 
        response = call_mistral_api(prompt, temperature=0.6, max_tokens=120) 
        if not response: 
            raise HTTPException(status_code=500, detail="Empty response from Mistral API") 
 
        question = sanitize_model_text(response.strip()) 
 
        if not question.endswith('?'): 
            question += '?' 
        if len(question.split()) > 40: 
            question = "Can you explain your experience related to this role in detail?" 
 
        logger.debug(f"Generated adaptive question: {question}") 
        return question 
 
    except Exception as e: 
        logger.error(f"Error generating question: {e}", exc_info=True) 
        return "Can you describe a challenging project you've handled and how you approached it?" 
 
def extract_question(text: str) -> str: 
    if not text or not isinstance(text, str): 
        raise HTTPException(status_code=400, detail="Invalid question text") 
    match = re.search(r"(?:Question[:\-]?\s*)?(.*\?)", text, re.IGNORECASE) 
    if match: 
        return match.group(1).strip() 
    raise HTTPException(status_code=400, detail="No valid question found") 
 
def text_to_speech(text: str, output_file: str) -> str: 
    logger.info("Converting text to speech: %s", text[:50] if text else "") 
    clean_text = sanitize_for_output(text) if text else "Please describe your experience" 
    try: 
        output_dir = os.path.dirname(output_file) 
        if output_dir: 
            os.makedirs(output_dir, exist_ok=True) 
        engine = pyttsx3.init() 
        engine.save_to_file(clean_text, output_file) 
        engine.runAndWait() 
        engine.stop() 
        time.sleep(1) 
        logger.info("TTS file saved: %s", output_file) 
        return output_file 
    except Exception as e: 
        logger.error("TTS error: %s", e, exc_info=True) 
        raise HTTPException(status_code=500, detail=f"Error converting text to speech: {str(e)}") 
 
def post_interview_details(user_id: str, role: str, job_description: str, experience_years: int, token: str) -> int: 
    logger.info("Posting interview details for user %s", user_id) 
    spring_api_url = f"{BASE_URL}/api/aiMocks/create" 
    payload = { 
        "user": {"id": user_id}, 
        "jobRole": role, 
        "jobDescription": sanitize_for_output(job_description), 
        "experience": experience_years 
    } 
    headers = {"Authorization": f"Bearer {token}"} if token else {} 
    try: 
        logger.info("Sending request to Spring API") 
        r = requests.post(spring_api_url, json=payload, headers=headers, timeout=60) 
        logger.info("Spring API response status: %s", r.status_code) 
        logger.debug("Spring API response: %s", r.text) 
        r.raise_for_status() 
        interview_id = r.json().get("id") 
        if not interview_id: 
            logger.error("Interview ID missing from Spring API response: %s", r.text) 
            raise HTTPException(status_code=500, detail="Interview ID missing from Spring API response") 
        logger.info("Interview created successfully with ID: %s", interview_id) 
        return interview_id 
    except Exception as e: 
        logger.error("Error posting interview details: %s", e) 
        return random.randint(1000, 9999) 
 
def store_question_audio(user_id: str, interview_id: str, audio_file_path: str, question: str, token: str) -> dict: 
    logger.info("Storing question audio for user %s, interview %s", user_id, interview_id) 
    spring_api_url = f"{BASE_URL}/api/AiMockQuestion/createQuestion" 
    try: 
        with open(audio_file_path, "rb") as audio_file: 
            files = {"audioFile": (os.path.basename(audio_file_path), audio_file, "audio/mpeg")} 
            data = { 
                "userId": user_id, 
                "aiMockId": interview_id, 
                "question": sanitize_for_output(question) 
            } 
            headers = {"Authorization": f"Bearer {token}"} if token else {} 
            response = requests.post(spring_api_url, data=data, files=files, headers=headers, timeout=120) 
            response.raise_for_status() 
            response_data = response.json() 
            qid = response_data.get("id") 
            url = response_data.get("audioUrl") 
            if not url: 
                url = f"http://example.com/questions/{user_id}_{interview_id}_question_0.mp3" 
            time.sleep(2) 
            os.remove(audio_file_path) 
            return {"question_id": qid, "audio_url": url} 
    except Exception as e: 
        logger.error("Error storing question audio: %s", e) 
        return { 
            "question_id": random.randint(1000, 9999), 
            "audio_url": f"http://example.com/questions/{user_id}_{interview_id}_question_0.mp3" 
        } 
 
def extract_text_from_pdf(pdf_path): 
    try: 
        text = "" 
        with pdfplumber.open(pdf_path) as pdf: 
            for page in pdf.pages: 
                page_text = page.extract_text() or "" 
                text += ultra_clean_text(page_text) + "\n" 
        return sanitize_for_output(text) 
    except Exception as e: 
        logger.error("Error extracting text from PDF: %s", e, exc_info=True) 
        raise HTTPException( 
            status_code=500, 
            detail=f"Error extracting text from PDF: {str(e)}" 
        ) 
 
def extract_text_from_docx(docx_path): 
    try: 
        text = "" 
        doc = Document(docx_path) 
        for p in doc.paragraphs: 
            text += ultra_clean_text(p.text) + "\n" 
        return sanitize_for_output(text) 
    except Exception as e: 
        logger.error("Error extracting text from DOCX: %s", e, exc_info=True) 
        raise HTTPException( 
            status_code=500, 
            detail=f"Error extracting text from DOCX: {str(e)}" 
        ) 
 
def extract_text_from_txt(txt_path): 
    try: 
        with open(txt_path, "r", encoding="utf-8") as f: 
            content = ultra_clean_text(f.read()) 
        return sanitize_for_output(content) 
    except Exception as e: 
        logger.error("Error extracting text from TXT: %s", e, exc_info=True) 
        raise HTTPException( 
            status_code=500, 
            detail=f"Error extracting text from TXT: {str(e)}" 
        ) 
 
def extract_text_from_file(file_path): 
    ext = os.path.splitext(file_path)[1].lower() 
    try: 
        if ext == ".pdf": 
            return extract_text_from_pdf(file_path) 
        elif ext == ".docx": 
            return extract_text_from_docx(file_path) 
        elif ext == ".txt": 
            return extract_text_from_txt(file_path) 
        else: 
            raise HTTPException( 
                status_code=400, 
                detail="Unsupported file format" 
            ) 
    except Exception as e: 
        logger.error("Error extracting text from file: %s", e, exc_info=True) 
        raise 
 
def parse_resume_with_nlp(text: str) -> dict: 
    result = {"skills": [], "experience": [], "education": []} 
    if not text: 
        return result 
    try: 
        doc = nlp(text) 
    except Exception: 
        return result 
    ents = getattr(doc, "ents", []) 
    noun_chunks = getattr(doc, "noun_chunks", []) 
    if ents: 
        for ent in ents: 
            label = getattr(ent, "label_", "").upper() 
            if label in ("ORG", "SCHOOL", "EDU", "EDUCATION", "DEGREE"): 
                result["education"].append(ent.text) 
            elif label in ("SKILL", "PERSONAL_SKILL", "NORP"): 
                result["skills"].append(ent.text) 
            elif label in ("DATE", "TIME", "DURATION"): 
                result["experience"].append(ent.text) 
    elif noun_chunks: 
        for chunk in noun_chunks: 
            txt = chunk.text.strip() 
            if 0 < len(txt.split()) <= 3: 
                result["skills"].append(txt) 
    return result 
 
def get_previous_answers(user_id: str, aimock_id: str, total_questions: int): 
    previous_answers = [] 
    for i in range(total_questions): 
        key = f"{user_id}:{aimock_id}:{i}" 
        try: 
            data = redis_client.get(key) 
            if data: 
                parsed = json.loads(data) 
                if isinstance(parsed, dict): 
                    previous_answers.append(parsed) 
        except Exception as e: 
            logger.error(f"Error parsing Redis data for key {key}: {e}") 
            continue 
    return previous_answers 
 
# --- Routes --- 
@app.post("/start") 
async def start_interview( 
    request: Request, 
    user_id: str = Form(...), 
    role: str = Form(...), 
    job_description: str = Form(...), 
    experience_years: int = Form(...), 
    resume: UploadFile = File(...), 
    token: str = Depends(get_token) 
): 
    try: 
        # Debug: Log the user_id and aimock_id 
        logger.info(f"Starting interview for user_id={user_id}") 
 
        resume_path = f"resumes/{user_id}_resume{os.path.splitext(resume.filename)[1]}" 
        os.makedirs("resumes", exist_ok=True) 
        with open(resume_path, "wb") as f: 
            f.write(await resume.read()) 
 
        resume_text = extract_text_from_file(resume_path) 
        extracted = parse_resume_with_nlp(resume_text) 
        base_level = determine_difficulty_level(experience_years) 
 
        # Post interview details to Spring backend 
        interview_id = post_interview_details(user_id, role, job_description, experience_years, token) 
        logger.info(f"Created interview with ID: {interview_id}") 
 
        # Store metadata in Redis 
        metadata = { 
            "user_id": user_id, 
            "interview_id": interview_id, 
            "role": role, 
            "current_question": 0, 
            "current_difficulty": base_level, 
            "skills": extracted.get("skills", []), 
            "experience": extracted.get("experience", []), 
            "education": extracted.get("education", []), 
            "job_description": sanitize_for_output(job_description), 
            "start_time": time.time(), 
            "scores": [] 
        } 
 
        # Generate first question (always an introduction question) 
        first_question = "Tell us about your background, experience, and how it relates to this role." 
 
        # Generate and store audio 
        audio_path = f"questions/{user_id}_{interview_id}_question_0.mp3" 
        os.makedirs("questions", exist_ok=True) 
        text_to_speech(first_question, audio_path) 
        store_resp = store_question_audio(user_id, interview_id, audio_path, first_question, token) 
 
        # Store metadata in Redis 
        metadata_key = f"{user_id}:{interview_id}:metadata" 
        redis_client.setex(metadata_key, timedelta(hours=48), json.dumps(metadata)) 
 
        # Clean up 
        shutil.rmtree("resumes", ignore_errors=True) 
 
        return JSONResponse(content={ 
            "message": "Interview started successfully.", 
            "aimock_id": interview_id, 
            "questions": [{ 
                "question_id": 0, 
                "question": first_question, 
                "audio_url": store_resp["audio_url"], 
                "difficulty": base_level 
            }] 
        }) 
    except Exception as e: 
        logger.error("Error starting interview: %s", e, exc_info=True) 
        raise HTTPException( 
            status_code=500, 
            detail=f"Error starting interview: {str(e)}" 
        ) 
 
@app.post("/submit") 
async def submit_answer( 
    request: Request, 
    submission: InterviewSubmissionRequest, 
    token: str = Depends(get_token) 
): 
    try: 
        user_id = submission.user_id 
        aimock_id = submission.aimock_id 
        question = submission.question 
        answer = submission.answer 
        finalize = submission.finalize 
 
        # Debug: Log the user_id and aimock_id 
        logger.info(f"Submitting answer for user_id={user_id}, aimock_id={aimock_id}") 
 
        # Debug: List all Redis keys 
        all_keys = redis_client.keys("*") 
        logger.info(f"All Redis keys: {all_keys}") 
 
        # Construct the Redis key 
        metadata_key = f"{user_id}:{aimock_id}:metadata" 
        logger.info(f"Fetching metadata with key: {metadata_key}") 
 
        # Fetch metadata from Redis 
        metadata_str = redis_client.get(metadata_key) 
        if not metadata_str: 
            logger.error(f"Metadata not found for key: {metadata_key}") 
            raise HTTPException(status_code=404, detail="Interview metadata not found") 
 
        # Parse metadata 
        metadata = json.loads(metadata_str) 
        logger.info(f"Fetched metadata: {metadata}") 
 
        # Rest of the logic 
        current_question = metadata.get("current_question", 0) 
        current_difficulty = metadata.get("current_difficulty", "intermediate") 
        scores = metadata.get("scores", []) 
 
        # Analyze the answer 
        analysis = analyze_answer_with_mistral(question, answer) 
 
        # Store answer in Redis 
        ans_key = f"{user_id}:{aimock_id}:answer:{current_question}" 
        redis_client.setex(ans_key, timedelta(hours=24), sanitize_for_output(answer)) 
 
        # Store score in Redis 
        score_key = f"{user_id}:{aimock_id}:scores:{current_question}" 
        redis_client.setex(score_key, timedelta(hours=48), json.dumps({ 
            "communication_score": analysis["communication_score"], 
            "technical_score": analysis["technical_score"], 
            "key_points": analysis["key_points"], 
            "suggested_followups": analysis["suggested_followups"] 
        })) 
 
        # Update scores 
        scores.append({ 
            "question": current_question, 
            "communication_score": analysis["communication_score"], 
            "technical_score": analysis["technical_score"] 
        }) 
        metadata["scores"] = scores[-3:] 
 
        # Get previous answers 
        previous_answers = get_previous_answers(user_id, aimock_id, current_question + 1) 
        previous_answers.append({ 
            "question": sanitize_for_output(question), 
            "answer": sanitize_for_output(answer), 
            "question_number": current_question 
        }) 
 
        # Finalize or generate next question 
        if not finalize: 
            new_difficulty = analysis.get("difficulty_recommendation", current_difficulty) 
            next_question = generate_interview_question( 
                role=metadata["role"], 
                job_description=metadata["job_description"], 
                skills=metadata["skills"], 
                experience=metadata["experience"], 
                education=metadata["education"], 
                question_number=current_question + 1, 
                difficulty=new_difficulty, 
                previous_answers=previous_answers 
            ) 
 
            # Generate and store audio 
            audio_path = f"questions/{user_id}_{aimock_id}_question_{current_question + 1}.mp3" 
            os.makedirs("questions", exist_ok=True) 
            text_to_speech(next_question, audio_path) 
            store_resp = store_question_audio(user_id, aimock_id, audio_path, next_question, token) 
 
            # Update metadata 
            metadata["current_question"] = current_question + 1 
            metadata["current_difficulty"] = new_difficulty 
            redis_client.setex(metadata_key, timedelta(hours=48), json.dumps(metadata)) 
 
            return JSONResponse(content={ 
                "message": "Answer processed successfully.", 
                "next_question": { 
                    "question_id": current_question + 1, 
                    "question": next_question, 
                    "audio_url": store_resp["audio_url"], 
                    "difficulty": new_difficulty 
                }, 
                "analysis": { 
                    "communication": analysis["communication_text"], 
                    "technical": analysis["technical_text"], 
                    "communication_score": analysis["communication_score"], 
                    "technical_score": analysis["technical_score"], 
                    "key_points": analysis["key_points"], 
                    "suggested_followups": analysis["suggested_followups"] 
                } 
            }) 
        else: 
            final_score = int((sum(s["communication_score"] + s["technical_score"] for s in scores) / (2 * len(scores))) * 100) if scores else 60 
            final_feedback = { 
                "communication": generate_feedback( 
                    "\n".join([analysis["communication_text"]] + [sanitize_for_output(str(kp)) for kp in analysis["key_points"]]), 
                    (sum(s["communication_score"] for s in scores) / len(scores)) * 100 if scores else 60 
                ), 
                "technical": generate_feedback( 
                    "\n".join([analysis["technical_text"]] + [sanitize_for_output(str(kp)) for kp in analysis["key_points"]]), 
                    (sum(s["technical_score"] for s in scores) / len(scores)) * 100 if scores else 60, 
                    is_technical=True 
                ), 
                "key_points": analysis["key_points"], 
                "suggested_followups": analysis["suggested_followups"] 
            } 
            return JSONResponse(content={ 
                "message": "Interview completed successfully.", 
                "mockscore": final_score, 
                "feedback": final_feedback 
            }) 
    except HTTPException: 
        raise 
    except Exception as e: 
        logger.error(f"Error submitting answer: {str(e)}", exc_info=True) 
        raise HTTPException(status_code=500, detail=f"Error processing answer: {str(e)}") 
 
# --- Entrypoint --- 
if __name__ == "__main__": 
    import uvicorn 
    logger.info("Starting Uvicorn server on 127.0.0.1:8003") 
    try: 
        uvicorn.run(app, host="127.0.0.1", port=8003) 
    except Exception as e: 
        logger.error("Error starting server: %s", e, exc_info=True) 
