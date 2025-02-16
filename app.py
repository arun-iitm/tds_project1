from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse, Response
import subprocess
import os,sys
import json
import time
import logging
from pathlib import Path
import openai
import requests
from dotenv import load_dotenv
from config import *

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI()


RUNNING_IN_CODESPACES = "CODESPACES" in os.environ
RUNNING_IN_DOCKER = os.path.exists("/.dockerenv")
logging.basicConfig(level=logging.INFO)

def ensure_local_path(path: str) -> str:
    """Ensure the path uses './data/...' locally, but '/data/...' in Docker."""
    if ((not RUNNING_IN_CODESPACES) and RUNNING_IN_DOCKER): 
        print("IN HERE",RUNNING_IN_DOCKER) # If absolute Docker path, return as-is :  # If absolute Docker path, return as-is
        return path
    
    else:
        logging.info(f"Inside ensure_local_path with path: {path}")
        return path.lstrip("/")  


# OpenAI API Configuration
AIPROXY_TOKEN = os.getenv("AIPROXY_TOKEN")
                     
if not AIPROXY_TOKEN:
    logging.error("AIPROXY_TOKEN is not set! Please set it as an environment variable.")
    raise ValueError("AIPROXY_TOKEN is not set! Please set it as an environment variable.")

client = openai.OpenAI(api_key=AIPROXY_TOKEN)

def install_dependencies(language: str, dependencies: list):
    """Install dependencies based on the language."""
    if not dependencies:
        logging.info("No dependencies to install.")
        return

    commands = {
        "python": ["pip", "install"],
        "node": ["npm", "install", "--global"],
        "bash": ["sudo", "apt-get", "install", "-y"]
    }
    
    if language in commands:
        try:
            logging.info(f"Installing {language} dependencies: {dependencies}")
            subprocess.run(commands[language] + dependencies, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            logging.error(f"Dependency installation failed: {e.stderr.strip()}")
            raise HTTPException(status_code=500, detail=f"Dependency installation failed: {e.stderr.strip()}")

def execute_code(llmcode: dict):
    """Execute code in the appropriate environment."""
    language = llmcode.get("language", "").lower()
    code = llmcode.get("code", "")

    if not code:
        logging.error("No code provided for execution.")
        return False, "No code provided."

    dependencies = llmcode.get("python_dependencies", []) if language == "python" else []
    install_dependencies(language, dependencies)

    commands = {
        "python": [sys.executable, "-c", code],
        "bash": ["bash", "-c", code],
        "node": ["node", "-e", code]
    }

    if language not in commands:
        logging.error(f"Unsupported language: {language}")
        return False, f"Unsupported language: {language}"

    try:
        result = subprocess.run(commands[language], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        if result.stderr.strip():
            logging.error(f"Execution error ({language}): {result.stderr.strip()}")
            return False, result.stderr.strip()

        logging.info(f"Code execution succeeded for {language}: {result.stdout.strip()}")
        return True, result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logging.error(f"Execution error ({language}): {e.stderr.strip()}")
        return False, e.stderr.strip()

def run_task_fix(task: str, llm_output: str, max_retries: int = 1):
    """Try executing the task, retrying with LLM fixes if errors occur."""
    attempt = 0
    while attempt <= max_retries:
        logging.info(f"Attempt {attempt + 1}/{max_retries} for task: {task}")
        
        try:
            llm_output = json.loads(llm_output) if isinstance(llm_output, str) else llm_output
        except json.JSONDecodeError:
            logging.error("Invalid JSON format in LLM response")
            return False
        
        success, error = execute_code(llm_output)
        if success:
            logging.info(f"Task '{task}' executed successfully.")
            return True
        
        time.sleep(1)  # Small delay before retrying
        attempt += 1

    logging.error(f"Task '{task}' failed after {max_retries} retries.")
    return False

def get_llm_response(task: str):
    """Fetch LLM response from OpenAI API."""
    try:
        response = requests.post(
            "http://aiproxy.sanand.workers.dev/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {AIPROXY_TOKEN}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT3},
                    {"role": "user", "content": task}
                ],
            },
            timeout=10
        )
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logging.error(f"OpenAI API error: {e}")
        return Response(status_code=500)

@app.post("/run")
async def run(task: str):
    """Handle task execution request."""
    try:
        logging.info(f"Received task request: {task}")
        gpt_answer_json = get_llm_response(task)
        success = run_task_fix(task, gpt_answer_json, max_retries=1)
        return {"status": "success" if success else "failure"}
    except KeyError as e:
        logging.error(f"Key error: {e}")
        raise HTTPException(status_code=400, detail=f"Key error: {e}")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")


@app.get("/read",response_class=PlainTextResponse)
async def read_file(path: str = Query(..., description="Path to the file to read")):
    logging.info(f"Inside read_file with path: {path}")
    output_file_path = ensure_local_path(path)
    if not os.path.exists(output_file_path):
        raise HTTPException(status_code=500, detail=f"Error executing function in read_file (GET API")
    with open(output_file_path, "r") as file:
        content = file.read()
    return PlainTextResponse(content)


@app.get("/")
def home():
    """Home endpoint."""
    return {"message": "Automation Agent for Project 1 Tasks"}

if __name__ == "__main__":
    logging.info("Starting FastAPI application...")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)