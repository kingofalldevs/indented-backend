import os
import subprocess
import tempfile
import time
from flask import Flask, request, Response, stream_with_context, jsonify
from flask_cors import CORS
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are Nova, a strict but helpful C++ Logic Mentor. Your mission is to help students find their own bugs through pinpointing and hints.

CURRICULUM (FOLLOW THIS SEQUENCE):
1. Module 1: Welcome to C++ (Introduction & Excitement)
2. Module 2: Anatomy of a C++ Program (headers, main, std::cout, stream operators)
3. Module 3: Data Types & Variables (int, float, char, string, bool, naming rules)
4. Module 4: Printing Text (Advanced std::cout, endl, \n, tracing logic)
5. Module 5: Arithmetic (+, -, *, /, % and double vs int division)
6. Module 6: Strings & Concatenation (string addition, mixing output)
7. Module 7: Checkpoint Quiz (5 Challenges covering Modules 1-6)
8. Module 8: Loops (for, while, do-while, real-world analogies)
9. Module 9: Arrays (declaration, indexing, processing with loops)
10. Module 10: Final Gauntlet (Complex real-world application)

CORE BEHAVIOR:
1. LESSON TRACKING: If a user is signed in, start from Module 1. Stay on track. Do not jump ahead until the student understands the current module.
2. MODULE COMPLETION: When the student demonstrates mastery of a module's concepts (especially the quiz in Module 7), you MUST output exactly: [[COMPLETED: module_id]] on its own line.
3. COMPILER-FIRST: Every time you see code, check for typos (std::ct, std::co, etc.), missing semicolons, or logic errors. 
4. PINPOINTING (MANDATORY): If there is an error, you MUST start your response with: [[ERROR: line_number]]. Count lines starting from 1.
5. HINTING: Explain conceptually what is wrong without giving the solution immediately.
6. BREVITY: Keep spoken responses to 2-3 sentences max. Be clinical and precise.
"""

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get("message", "")
    history = data.get("history", [])
    user_name = data.get("user_name", "Student")
    current_code = data.get("current_code", "")

    # Format code with line numbers to help Nova count accurately
    numbered_code = ""
    if current_code:
        numbered_code = "\n".join([f"{i+1}: {line}" for i, line in enumerate(current_code.split('\n'))])

    # Combine system prompt and the current code context into one message to prevent leakage
    context_message = f"{SYSTEM_PROMPT}\n\n### USER'S CURRENT CODE (FOR YOUR REFERENCE ONLY - DO NOT REPEAT):\n{numbered_code}\n###\n\nRemember: Only provide the hint and the [[ERROR: line]] tag. Never repeat the code above."

    # Build message list for Groq
    messages = [
        {"role": "system", "content": context_message}
    ]

    if data.get("is_signed_in"):
        messages.append({
            "role": "system", 
            "content": "The user is SIGNED IN. You must strictly follow the CURRICULUM sequence (Module 1, then 2, then 3...) and only proceed when they master each step. If they seem confused, go back and re-teach the current module before moving on."
        })
    else:
        messages.append({
            "role": "system",
            "content": "The user is a GUEST. You can answer general questions but encourage them to sign in to start the formal C++ curriculum."
        })

    # Add conversation history (last 5 exchanges to keep it very focused)
    for msg in history[-5:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    # Add the new user message
    messages.append({"role": "user", "content": user_message})

    def generate():
        try:
            stream = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=messages,
                stream=True,
                max_tokens=600,
                temperature=0.6,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            print(f"ERROR: {str(e)}")
            yield "Indie is currently experiencing a technical hiccup (API issue). Please try again in a moment."

    return Response(
        stream_with_context(generate()),
        content_type="text/plain; charset=utf-8"
    )

@app.route("/api/health", methods=["GET"])
def health():
    return {"status": "Indie online", "model": "llama-3.1-8b-instant"}

@app.route("/api/run", methods=["POST"])
def run_code():
    data = request.json
    code = data.get("code", "")
    stdin_input = data.get("stdin", "")
    
    if not code:
        return jsonify({"output": "No code provided."}), 400
        
    try:
        # Secure isolation via Temporary Directory
        with tempfile.TemporaryDirectory() as temp_dir:
            source_file = os.path.join(temp_dir, "main.cpp")
            output_bin = os.path.join(temp_dir, "main")
            
            with open(source_file, "w") as f:
                f.write(code)
                
            # Compile Code
            compile_process = subprocess.run(
                ["g++", source_file, "-o", output_bin],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if compile_process.returncode != 0:
                clean_err = compile_process.stderr.replace(temp_dir + "/", "")
                return jsonify({"output": "Compilation Error:\n" + clean_err}), 200
                
            # Execute Binary with Popen
            process = subprocess.Popen(
                [output_bin],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            
            # Set pipes to non-blocking to allow reading without hanging
            os.set_blocking(process.stdout.fileno(), False)
            os.set_blocking(process.stderr.fileno(), False)

            stdout_data = ""
            stderr_data = ""
            start_time = time.time()
            timeout = 2
            
            try:
                # Send the provided input but DO NOT close stdin yet
                if stdin_input:
                    process.stdin.write(stdin_input)
                    process.stdin.flush()
                
                # Poll for completion or timeout
                while time.time() - start_time < timeout:
                    # Read available stdout
                    try:
                        chunk = os.read(process.stdout.fileno(), 4096)
                        if chunk: stdout_data += chunk.decode('utf-8', errors='replace')
                    except (BlockingIOError, IOError):
                        pass
                        
                    # Read available stderr
                    try:
                        chunk = os.read(process.stderr.fileno(), 4096)
                        if chunk: stderr_data += chunk.decode('utf-8', errors='replace')
                    except (BlockingIOError, IOError):
                        pass

                    if process.poll() is not None:
                        break
                    time.sleep(0.05)
                else:
                    # Timeout reached
                    if process.poll() is None:
                        # Process still alive, likely waiting for more input
                        process.terminate()
                        # Final quick read after termination
                        time.sleep(0.05)
                        try:
                            chunk = os.read(process.stdout.fileno(), 4096)
                            if chunk: stdout_data += chunk.decode('utf-8', errors='replace')
                        except: pass
                        
                        return jsonify({
                            "output": stdout_data if stdout_data else "// Waiting for input...",
                            "waiting_for_input": True
                        }), 200
                
                # If we are here, the process finished
                final_output = stdout_data
                if stderr_data:
                    clean_run_err = stderr_data.replace(temp_dir + "/", "")
                    final_output += "\n[Error Output]:\n" + clean_run_err
                    
                if not final_output.strip():
                    final_output = "// Program ran with no output"
                    
                return jsonify({"output": final_output}), 200

            except Exception as e:
                if process.poll() is None:
                    process.kill()
                raise e
            
    except Exception as e:
        return jsonify({"output": f"Server Error: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5051))
    print(f"🔵 Indie backend starting on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=True)
