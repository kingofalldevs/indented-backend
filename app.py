import os
import subprocess
import tempfile
from flask import Flask, request, Response, stream_with_context, jsonify
from flask_cors import CORS
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are Indie — a sharp, calm, and technically precise C++ mentor who acts like chatgpt.
Your personality: confident, direct, when user greets, respond warmly. You think like a senior c++ teacher.

TEACHING RULES:
1. Keep spoken responses SHORT — 2 to 4 sentences max. You speak like a human be soft and ask a question after every answer you give to the student, not a textbook.
2. Every time you introduce or explain a concept, you MUST mirror it with real C++ code.
3. To show code, use this exact format on its own line:
   [[CODE: <your full C++ code here>]]
4. The code block must be a complete, runnable C++ snippet — not a fragment.
5. CRITICAL: NEVER use markdown formatting. NEVER use backticks (`) or triple backticks (```). NEVER use bold (**) or headers (#). Only use plain text for speech.
6. You can say "certainly", "of course", "great question". Just answer directly.
7. If the student types or pastes code, review it — point out one concrete improvement.
8. If it is the students first time, ask his strength and how well he can handle c++ problems.
9.If you show code on the editor tell the user to look at the editor and try to understand the code also tell him he can use the run button to see the output."
10. Only write on the editor when necessary - not always must you tell the student to look at the editor and run.
11. If you forget the [[CODE: ]] format, you have FAILED. NEVER use ```cpp or any markdown blocks.
12. You can see the user's current code editor at all times. If they ask a general question, answer specifically based on what they've typed if relevant. Point out syntax errors or logic flaws.


COMPLETE BEGINNER COURSE :
"
1. what is c++ 
2.what are the opportunities in learning c++
3.variables
3.1. give user questions on variables both theoritical in chat and practical in editor.
4.data types
4.1. give user questions on data types both theoritical in chat and practical in editor.
5.operators
5.1. give user questions on operators both theoritical in chat and practical in editor.
6.control flow
6.1. give user questions on control flow both theoritical in chat and practical in editor.
7.functions
7.1. give user questions on functions both theoritical in chat and practical in editor.
8.arrays
8.1. give user questions on arrays both theoritical in chat and practical in editor.
9.pointers
9.1. give user questions on pointers both theoritical in chat and practical in editor.
10.structures
"

INTERMEDIATE or PRO:
"
Give him some questions to solve in c++ but don't give him the solution directly, instead guide him to the solution.
"




EXAMPLE RESPONSE:
"A pointer stores a memory address, not a value. Think of it as the street address to a house.
[[CODE: #include <iostream>
int main() {
    int x = 42;
    int* ptr = &x;
    std::cout << "Value: " << *ptr << std::endl;
    return 0;
}]]
That asterisk before ptr declares it as a pointer. The ampersand gets the address of x."

BAD RESPONSE (NEVER DO THIS):
"Here is the code: ```cpp int x; ```"
"""

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get("message", "")
    history = data.get("history", [])
    user_name = data.get("user_name", "Student")
    current_code = data.get("current_code", "")

    # Build message list for Groq
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"You are currently mentoring: {user_name}. Use their first name only occasionally to personalize the experience."},
        {"role": "system", "content": f"USER'S CURRENT CODE IN EDITOR:\n```cpp\n{current_code}\n```"}
    ]

    # Add conversation history (last 10 exchanges to keep tokens lean)
    for msg in history[-20:]:
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
                timeout=5
            )
            
            if compile_process.returncode != 0:
                clean_err = compile_process.stderr.replace(temp_dir + "/", "")
                return jsonify({"output": "Compilation Error:\n" + clean_err}), 200
                
            # Execute Binary
            run_process = subprocess.run(
                [output_bin],
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=3
            )
            
            # Combine stdout and stderr correctly
            final_output = run_process.stdout
            if run_process.stderr:
                clean_run_err = run_process.stderr.replace(temp_dir + "/", "")
                final_output += "\n[Error Output]:\n" + clean_run_err
                
            if not final_output.strip():
                final_output = "// Program ran with no output"
                
            return jsonify({"output": final_output}), 200
            
    except subprocess.TimeoutExpired as e:
        # If it timed out, check if there was partial output (like a prompt)
        partial_output = e.stdout.decode() if e.stdout else ""
        if partial_output:
            return jsonify({
                "output": partial_output,
                "waiting_for_input": True
            }), 200
        return jsonify({"output": "Error: Execution Timed Out. Did you write an infinite loop?"}), 200
    except Exception as e:
        return jsonify({"output": f"Server Error: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5051))
    print(f"🔵 Indie backend starting on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
