import logging
import os
import sys
import threading
import openai
import json
import time
import random
from datetime import datetime
from flask import Flask, render_template, request, jsonify, url_for
from dotenv import load_dotenv
from test_runner import TestRunner
from queue import Queue
from threading import Thread, Lock
from typing import List, Tuple


load_dotenv()

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
log = logging.getLogger("werkzeug")
log.disabled = True

test_runner = TestRunner()

# Set the OpenAI API key
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OPENAI_API_KEY not found in environment variables")
client = openai.OpenAI(api_key=api_key)  # instantiate client with API key

# Data structure to store prompts and page mappings
# This dictionary maps page_name -> {"prompt": ..., "timestamp": ...}
metadata_file = "page_metadata.json"

# Queue for storing prompts
prompt_queue = Queue()
queue_lock = Lock()
is_processing = False


def process_queue():
    global is_processing
    while True:
        try:
            if not prompt_queue.empty():
                with queue_lock:
                    is_processing = True
                    prompt = prompt_queue.get()

                sys.stdout.write(f"\nProcessing prompt: {prompt}\n")
                sys.stdout.flush()

                success, result = create_page(prompt)

                if success:
                    sys.stdout.write(f"\nSuccess! Page created: {result}\n")
                    sys.stdout.write("A new button has been added to the index page.\n")
                else:
                    sys.stdout.write(f"\nError: {result}\n")
                sys.stdout.flush()

                with queue_lock:
                    prompt_queue.task_done()
                    is_processing = False
            else:
                time.sleep(0.1)  # Prevent CPU spinning
        except Exception as e:
            logger.error(f"Error in queue processing: {e}")
            with queue_lock:
                is_processing = False
            time.sleep(1)  # Brief pause before continuing


def get_queue_status() -> Tuple[int, bool]:
    """Returns (number of items in queue, whether processing is active)"""
    with queue_lock:
        return prompt_queue.qsize(), is_processing


def load_metadata():
    """Load page metadata from a JSON file."""
    if not os.path.exists(metadata_file):
        return {}
    with open(metadata_file, "r", encoding="utf-8") as f:
        return json.load(f)


def save_metadata(metadata):
    """Save page metadata to a JSON file."""
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)


page_metadata = load_metadata()


def store_page_info(page_name, prompt):
    """Store page information in the metadata dictionary and file."""
    page_metadata[page_name] = {
        "prompt": prompt,
        "timestamp": time.time(),  # store timestamp as a float
    }
    save_metadata(page_metadata)


def get_page_info(page_name):
    """Retrieve page information from the metadata dictionary."""
    return page_metadata.get(page_name, {"prompt": None, "timestamp": None})


def get_available_pages():
    pages_dir = os.path.join(app.root_path, "templates", "pages")
    if not os.path.exists(pages_dir):
        os.makedirs(pages_dir)
    pages = [
        f.replace(".html", "") for f in os.listdir(pages_dir) if f.endswith(".html")
    ]
    return pages


@app.route("/api/sms/webhook", methods=["POST"])
def sms_webhook():
    try:
        # Get the raw data from the webhook
        data = request.form

        # Extract the message content
        message = data.get("MESSAGE", "")
        from_number = data.get("FROM", "unknown")

        if not message:
            logger.error("Empty message received from webhook")
            return "OK", 200

        # Add the message to our existing queue
        prompt_queue.put(message)

        # Log the incoming message
        logger.info(
            f"SMS Received - From: {from_number}, Message: {message}, Queue Position: {prompt_queue.qsize()}"
        )

        # Always return OK to the SMS service
        return "OK", 200

    except Exception as e:
        logger.error(f"Webhook Error: {e}")
        # Still return OK to prevent retries
        return "OK", 200


@app.template_filter("datetimeformat")
def datetimeformat_filter(value):
    """
    A custom Jinja2 filter to format Unix timestamps into human-readable strings.
    If value is None or invalid, returns an empty string.
    """
    if value is None:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


@app.route("/")
def index():
    pages = get_available_pages()
    # Sort pages by timestamp if available for chronological order
    sorted_pages = sorted(
        pages, key=lambda p: get_page_info(p)["timestamp"] or 0, reverse=True
    )
    page_info_list = []
    for p in sorted_pages:
        info = get_page_info(p)
        page_info_list.append(
            {"name": p, "prompt": info["prompt"], "timestamp": info["timestamp"]}
        )
    return render_template("index.html", pages=page_info_list)


@app.route("/pages/<page_name>")
def serve_page(page_name):
    """Serve the dynamically generated page."""
    # Ensure the requested file exists
    pages_dir = os.path.join(app.root_path, "templates", "pages")
    page_path = os.path.join(pages_dir, f"{page_name}.html")
    if not os.path.exists(page_path):
        return "Page not found", 404
    return render_template(f"pages/{page_name}.html")


@app.route("/api/llm/generate", methods=["POST"])
def generate_page_endpoint():
    """Dedicated endpoint for page generation"""
    data = request.get_json()
    prompt = data.get("prompt", "")
    try:
        success, result = create_page(prompt)
        if success:
            return jsonify({"success": True, "page_name": result})
        else:
            return jsonify({"success": False, "error": result}), 400
    except Exception as e:
        logger.error(f"Page generation error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/llm/interact", methods=["POST"])
def llm_interaction_endpoint():
    data = request.get_json()

    # Enforce required fields
    if not data.get("role") or not data.get("prompt"):
        return jsonify({"success": False, "error": "Missing required fields"}), 400

    try:
        # Parse expected response type
        expected_type = data.get("expect", "text")  # text, list, or json

        # Build system message based on expected type
        system_message = f"""You are {data['role']}. 
You must respond in this exact format:
{expected_type=='text' and 'A single text string' or 
 expected_type=='list' and 'A JSON array of strings' or 
 expected_type=='json' and 'A JSON object matching the provided schema'}
"""

        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": data["prompt"]},
            ],
            temperature=data.get("temperature", 0.7),
        )

        result = response.choices[0].message.content.strip()

        # Validate and parse response based on expected type
        if expected_type == "json":
            try:
                result = json.loads(result)
            except:
                return jsonify(
                    {"success": False, "error": "Invalid JSON response"}
                ), 500
        elif expected_type == "list":
            try:
                result = json.loads(result)
                if not isinstance(result, list):
                    raise ValueError("Not a list")
            except:
                return jsonify(
                    {"success": False, "error": "Invalid list response"}
                ), 500

        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"LLM interaction error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/llm/page", methods=["POST"])
def page_llm_endpoint():
    """Simple endpoint for generated pages to interact with LLM"""
    data = request.get_json()
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": "You are " + data.get("role", "a helpful AI assistant."),
                },
                {"role": "user", "content": data.get("prompt", "")},
            ],
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 4096),
            n=1,
        )
        return jsonify(
            {"success": True, "data": response.choices[0].message.content.strip()}
        )
    except Exception as e:
        logger.error(f"Page LLM interaction error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def create_page(prompt: str) -> tuple[bool, str]:
    system_message = """
You are a tool that generates HTML pages with inline CSS and JS based made to fulfill the request of the user prompt. You will as per the rules something that will be a complete fully functional page, each part of it must be fully implemented and working; if the user's request via prompt is too large to handle easily, you need to be creative and find a way to meet the demands to make it still work somehow, even if you have to be cheeky about it. You can use the LLM integration if the user asks for it or for something that logically should be handled by the LLM. You will follow these strict rules:

STRUCTURE:
- Start with <!DOCTYPE html>
- Include required meta tags in <head>
- All CSS in <style> within <head>
- All content within <body>
- All JavaScript in <script> at end of <body>
- Everything must be inline - no external resources

TOKEN MANAGEMENT:
- Target response size: 4000 tokens
- If feature would exceed limit:
  1. Implement simpler version
  2. Use efficient alternatives
  3. Focus on core functionality
  4. Be creative with space-saving solutions
- All features must be complete, even if simplified

ABSOLUTELY CRITICAL:
1. NO TODO COMMENTS
2. NO PLACEHOLDER CODE
3. NO UNFINISHED LOGIC
4. EVERY FEATURE MUST BE FULLY IMPLEMENTED
5. EVERY EVENT HANDLER MUST BE CONNECTED
6. EVERY VARIABLE MUST BE INITIALIZED AND USED
7. EVERY FUNCTION MUST BE COMPLETE

LLM INTEGRATION:
If your page needs LLM interaction, you must write a wrapper function. The endpoint expects:
Input:
{
    "role": "what the AI should be (e.g., 'a story generator', 'a math problem creator')",
    "prompt": "what you want the AI to do",p
    "temperature": optional number 0-1,
    "max_tokens": optional number
}

Output:
{
    "success": true/false,
    "data": "the AI response" or
    "error": "error message if failed"
}

Every page using LLM MUST use this exact structure - no exceptions:

class AIHandler {
    constructor(role, expectedType = 'text') {
        this.role = role;
        this.expectedType = expectedType;
        this.elements = {
            app: document.getElementById('app'),  // Add this
            content: document.getElementById('content'),
            loader: document.getElementById('loader'),
            error: document.getElementById('error'),
            input: document.getElementById('input')  // Add this for input controls
        };
        
        // Validate all elements exist
        Object.entries(this.elements).forEach(([key, el]) => {
            if (!el) throw new Error(`Required element #${key} not found`);
        });
    }

    async generate(prompt) {
        try {
            this._setLoading(true);
            const response = await fetch('/api/llm/interact', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    role: this.role,
                    prompt,
                    expect: this.expectedType
                })
            });
            
            const result = await response.json();
            if (!result.success) throw new Error(result.error);
            return result.data;
        } catch (error) {
            this._handleError(error);
            throw error;
        } finally {
            this._setLoading(false);
        }
    }

    _setLoading(isLoading) {
        this.elements.loader.style.display = isLoading ? 'block' : 'none';
        this.elements.input.style.opacity = isLoading ? '0.5' : '1';
        this.elements.input.style.pointerEvents = isLoading ? 'none' : 'auto';
        document.querySelectorAll('button, input').forEach(el => 
            el.disabled = isLoading
        );
    }

    _handleError(error) {
        console.error(error);
        this.elements.error.textContent = error.message;
        this.elements.error.style.display = 'block';
        setTimeout(() => this.elements.error.style.display = 'none', 5000);
    }
}

Every page must include:
<div id="app">
    <div id="content"></div>
    <div id="loader">Loading...</div>
    <div id="error"></div>
</div>

Every page with LLM interaction must use:
const ai = new AIHandler({
    role: "your role here",
    responseType: "string|array|object",
    schema: {
        type: "string|array|object",
        properties: {} // for objects
    }
});

No exceptions. No modifications. This exact structure or the page will fail.

Write your own wrapper based on your specific needs. Remember the AI role is already prefixed with "You are", so just specify what follows. Use the LLM if you're instructed to do so or to generate complexity that you can't fit into your single-page of html/js/css; be creative with it, the LLM integration with the correct wrappers can write text, code/css/js.

For example: Instead of trying to store large amount of scenarios or complex content, you can:
1. Code the core mechanics (variables, UI, basic logic)
2. Write a wrapper that uses the LLM to generate content based on those variables
3. Let the LLM handle the complex/creative parts while your code handles the mechanics
4. This is ONLY if it's necessary, don't use the LLM if not needed.

GRAPHICS AND RESOURCES:
1. CSS-Only Graphics (Preferred):
   - Pure CSS shapes, gradients, animations
   - ::before/::after elements for complex shapes 
   - CSS Grid/Flex for layouts
   - Transform/transition for animations
   - IMPORTANT: When implementing figures/drawings:
     * Break down into individual elements
     * Each part must be a separate div
     * Use absolute positioning for placement
     * Include all states/variations upfront
     * No reliance on SVG paths unless specifically needed
     * Test all visual states before completion

2. SVG Graphics (When needed):
   - Must include complete paths
   - All elements must be properly grouped
   - Include viewBox and dimensions
   - Test all states and animations
   - Provide CSS fallback

2. Verified CDN Resources Only:
   - FontAwesome: "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css"
   - Material Icons: "https://fonts.googleapis.com/icon?family=Material+Icons"
   - Bootstrap Icons: "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.7.2/font/bootstrap-icons.css"
   - Bootstrap CSS: "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"
   - Google Fonts: "https://fonts.googleapis.com/css2?family=..."

3. Canvas Generation:
   - For dynamic/complex graphics
   - Must include error handling
   - Must have fallback display

4. Emoji/Unicode:
   - For simple icons/graphics
   - Must include font-family fallbacks

VALIDATION CHECKLIST:
✓ Self-contained graphics only
✓ Verified CDN links only
✓ Include fallbacks
✓ Responsive design
✓ Error handling
✓ Cross-browser support

Start simple, add complexity only if needed, always have fallbacks!

CRITICAL RULES:
- No placeholder content - everything must work
- No TODO logic, no unfinished parts; if the prompt is requesting something huge, find creative ways of fulfilling it and making a finished product. It MUST be complete.
- All features must be fully implemented, but if the requested feature is complex, you can use the LLM to generate content
- All error states must be handled
- All async operations must show loading state
- All UI changes must provide feedback
- If using LLM, always handle API errors and timeouts
- If using LLM, always show loading states during calls
- If using LLM, always validate and sanitize responses
- When implementing features, consider what a user would reasonably expect from their request and implement the most natural and intuitive behavior.

The page you generate must be completely self-contained and fully functional.
"""

    analysis_message = """You are a code analyzer. You've been given:
1. The original prompt: what the page should do
2. The generated code: an attempt to fulfill that prompt

Your ONLY task is to respond with:
1. First line: TRUE if the code needs fixes, FALSE if it works perfectly
2. Second line: Brief comma-separated list of specific issues (if TRUE)
3. Third line onwards: Nothing - keep it concise

Example responses:
FALSE
(if code works perfectly)

or

TRUE
event handlers not connected, map array not initialized, move() function incomplete
"""

    fix_message = """You are a surgical code fixer. Your response must be a complete HTML page (max 4000 tokens).

Original Request: {prompt}
Issues to Fix: {issues}

RESPONSE FORMAT:
<!DOCTYPE html>
<html>
... complete working page ...
</html>

RULES:
1. Preserve all working code exactly as-is
2. Only modify/complete the identified issues
3. Keep existing structure and variable names
4. Stay within 4000 tokens
5. If a feature would exceed limits, implement a simpler version that works
6. No TODOs or placeholders - everything must work
7. Include all required elements (app, content, loader, error)

If a requested feature is too complex:
1. Implement a simpler but complete version
2. Use creative alternatives (e.g., CSS instead of SVG)
3. Break complex features into core + optional parts
4. Prioritize working functionality over complexity

Your response must be ONLY the complete, working HTML page."""

    user_message_content = (
        f"Create a web page that: {prompt}\n"
        "The page must strictly follow the template and rules outlined in the system message.\n"
        "Ensure that all functionalities are accurately implemented and will work straight away, there are no second chances.\n"
    )

    max_attempts = 5
    for attempt in range(max_attempts):
        logger.debug(f"Attempt {attempt + 1}/{max_attempts}: Sending prompt to OpenAI")

        try:
            # Initial page generation
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message_content},
                ],
                temperature=0.2,
                max_tokens=4096,
                n=1,
            )

            page_content = response.choices[0].message.content.strip()
            if "```html" in page_content:
                page_content = page_content.split("```html")[1].split("```")[0].strip()

            # Analyze the generated code
            analysis_response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": analysis_message},
                    {
                        "role": "user",
                        "content": f"Original prompt: {prompt}\n\nGenerated code:\n{page_content}",
                    },
                ],
                temperature=0.1,
                max_tokens=200,
            )

            analysis_result = (
                analysis_response.choices[0].message.content.strip().split("\n")
            )
            needs_fixes = analysis_result[0].upper() == "TRUE"

            if needs_fixes and len(analysis_result) > 1:
                issues = analysis_result[1]
                logger.debug(f"Issues found: {issues}")

                # Fix the issues while preserving working parts
                fix_response = client.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {
                            "role": "system",
                            "content": fix_message.format(prompt=prompt, issues=issues),
                        },
                        {"role": "user", "content": page_content},
                    ],
                    temperature=0.1,
                    max_tokens=4096,
                )

                fixed_content = fix_response.choices[0].message.content.strip()
                if "```html" in fixed_content:
                    fixed_content = (
                        fixed_content.split("```html")[1].split("```")[0].strip()
                    )

                if fixed_content.startswith("<!DOCTYPE html>"):
                    page_content = fixed_content

            # Ensure all required elements are present
            if not page_content.startswith("<!DOCTYPE html>"):
                page_content = "<!DOCTYPE html>\n" + page_content

            if "<head>" not in page_content:
                page_content = page_content.replace("<html>", "<html>\n<head></head>")

            head_end = page_content.find("</head>")
            if head_end != -1:
                meta_tags = ""
                if '<meta charset="UTF-8">' not in page_content:
                    meta_tags += '<meta charset="UTF-8">\n'
                if '<meta name="viewport"' not in page_content:
                    meta_tags += '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                page_content = (
                    page_content[:head_end] + meta_tags + page_content[head_end:]
                )

            # Test the final page
            success, error = test_runner.test_page(page_content)
            if success:
                page_name = f"page_{int(time.time())}_{random.randint(1000, 9999)}"
                pages_dir = os.path.join(app.root_path, "templates", "pages")
                page_path = os.path.join(pages_dir, f"{page_name}.html")
                with open(page_path, "w", encoding="utf-8") as f:
                    f.write(page_content)

                logger.info(f"Page successfully created and saved as: {page_path}")
                store_page_info(page_name, prompt)
                return True, page_name
            else:
                logger.warning(
                    f"Attempt {attempt + 1} failed. Error: {error}. Retrying...\n"
                )
                if attempt < max_attempts - 1:
                    user_message_content = (
                        f"The previous attempt produced an error: {error}\n"
                        "Please correct these issues and produce the corrected HTML.\n"
                        f"Original prompt: {prompt}\n"
                    )
                else:
                    logger.error(
                        f"Failed after {max_attempts} attempts. Last error: {error}"
                    )
                    return (
                        False,
                        f"Failed after {max_attempts} attempts. Last error: {error}",
                    )

        except openai.OpenAIError as e:
            logger.error(f"OpenAI API Error on attempt {attempt + 1}: {e}")
            if attempt == max_attempts - 1:
                return False, f"OpenAI error after {max_attempts} attempts: {str(e)}"
        except Exception as e:
            logger.error(f"General error on attempt {attempt + 1}: {e}")
            if attempt == max_attempts - 1:
                return False, str(e)

    return False, "Unexpected failure to generate a valid page after all attempts."


def prompt_loop():
    sys.stdout.write("\nWeb app is running. Access it at http://localhost:5000\n")
    sys.stdout.write("\nEnter your page descriptions below. Type 'quit' to exit.\n")
    sys.stdout.write("Type 'status' to see the queue status.\n")
    sys.stdout.flush()

    while True:
        try:
            queue_size, processing = get_queue_status()
            status = " [Processing]" if processing else ""
            if queue_size > 0:
                status += f" [Queue: {queue_size}]"

            sys.stdout.write(f"\n>{status} ")
            sys.stdout.flush()
            prompt_input = input().strip()

            if prompt_input.lower() == "quit":
                sys.stdout.write("\nShutting down...\n")
                sys.stdout.flush()
                os._exit(0)

            if prompt_input.lower() == "status":
                queue_size, processing = get_queue_status()
                status_msg = f"\nQueue status:\n"
                status_msg += f"Items in queue: {queue_size}\n"
                status_msg += f"Currently processing: {'Yes' if processing else 'No'}\n"
                sys.stdout.write(status_msg)
                sys.stdout.flush()
                continue

            if not prompt_input:
                sys.stdout.write("Please provide a valid prompt.\n")
                sys.stdout.flush()
                continue

            prompt_queue.put(prompt_input)
            sys.stdout.write(
                f"Prompt added to queue. Position: {prompt_queue.qsize()}\n"
            )
            sys.stdout.flush()

        except KeyboardInterrupt:
            sys.stdout.write("\nExiting...\n")
            sys.stdout.flush()
            os._exit(0)
        except Exception as e:
            logger.exception("Error in prompt loop.")
            sys.stdout.write(f"\nError: {e}\n")
            sys.stdout.flush()


if __name__ == "__main__":
    from waitress import serve

    # Start the queue processor thread
    queue_processor = threading.Thread(target=process_queue, daemon=True)
    queue_processor.start()

    # Start the Flask app in a separate thread
    flask_thread = threading.Thread(
        target=lambda: serve(app, host="0.0.0.0", port=5000), daemon=True
    )
    flask_thread.start()

    # Run the prompt loop in the main thread
    prompt_loop()
