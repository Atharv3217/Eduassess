from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import random
import google.generativeai as genai
import PyPDF2, docx
import os
from datetime import datetime
from flask_cors import CORS
from google.cloud import storage

app = Flask(__name__)
CORS(app)  # Add this line after initializing your Flask app

# Cloud Storage and Database Configuration
BUCKET_NAME = "storied-surface-448310-a9-database"
DB_PATH = "/tmp/database.db"
GCS_DB_PATH = "database.db"
LOCAL_DB_COPY = "database.db" 

# Ensure the 'uploads' directory exists in /tmp (writable in Cloud environments)
UPLOADS_DIR = "/tmp/uploads"
if not os.path.exists(UPLOADS_DIR):
    os.makedirs(UPLOADS_DIR)

# Configure Gemini AI
genai.configure(api_key="AIzaSyDnfpE0dqSjC3CC7lx5LXcZ1DMmGumsO-s")

def download_db():
    """Download the database from Cloud Storage or copy from local DB"""
    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(GCS_DB_PATH)

    if not os.path.exists(DB_PATH):  # Only copy/download if it doesn't exist
        try:
            print("📥 Attempting to download database from GCS...")
            blob.download_to_filename(DB_PATH)
            print("✅ Database downloaded from GCS successfully!")
        except Exception as e:
            print(f"⚠️ Failed to download from GCS ({e}). Copying local database...")
            if os.path.exists(LOCAL_DB_COPY):  # Check if local database exists
                os.system(f"cp {LOCAL_DB_COPY} {DB_PATH}")
                print("✅ Database copied from local file!")
            else:
                print("❌ ERROR: Local database.db file not found!")

def upload_db():
    """Upload the database to Cloud Storage"""
    try:
        client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(GCS_DB_PATH)
        blob.upload_from_filename(DB_PATH)
        print("✅ Database uploaded to GCS successfully!")
    except Exception as e:
        print(f"⚠️ Failed to upload to GCS: {e}")

# Connect to SQLite Database
def connect_db():
    """Connect to the SQLite database"""
    return sqlite3.connect(DB_PATH, check_same_thread=False)

# Initialize database
download_db()

# Initialize tables
def create_table():
    with connect_db() as conn:
        cursor = conn.cursor()
        
        # Create quizzes table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quizzes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT NOT NULL
            )
        ''')

        # Create questions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id INTEGER,
                class_level TEXT,
                subject TEXT,
                book_name TEXT,
                chapter TEXT,
                question TEXT NOT NULL,
                option1 TEXT NOT NULL,
                option2 TEXT NOT NULL,
                option3 TEXT NOT NULL,
                option4 TEXT NOT NULL,
                correct_answer TEXT NOT NULL,
                FOREIGN KEY (quiz_id) REFERENCES quizzes(id)
            )
        ''')

        # Create game_sessions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS game_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id INTEGER NOT NULL,
                pin INTEGER NOT NULL,
                status TEXT DEFAULT "active",
                current_question_index INTEGER DEFAULT 0,
                started_for_all INTEGER DEFAULT 0,
                FOREIGN KEY (quiz_id) REFERENCES quizzes(id)
            )
        ''')

        # Create participants table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_name TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                email_id TEXT NOT NULL,
                district TEXT NOT NULL,
                game_pin INTEGER NOT NULL
            )
        ''')

        # Create responses table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_pin INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                participant TEXT NOT NULL,
                answer TEXT NOT NULL,
                is_correct INTEGER NOT NULL,
                FOREIGN KEY (question_id) REFERENCES questions(id)
            )
        ''')

        # Create new_questions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS new_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_level TEXT NOT NULL,
                subject TEXT NOT NULL,
                book_name TEXT NOT NULL,
                chapter TEXT NOT NULL,
                question TEXT NOT NULL,
                option1 TEXT NOT NULL,
                option2 TEXT NOT NULL,
                option3 TEXT NOT NULL,
                option4 TEXT NOT NULL,
                correct_answer TEXT NOT NULL
            )
        ''')
            
        conn.commit()

create_table()

# Dictionary to store waiting participants
waiting_participants = {}  # Dict of game_pin -> list of participants

# Extract Text from Uploaded File
def extract_text_from_file(file_path):
    text = ""
    if file_path.endswith(".pdf"):
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text += page.extract_text() + "\n"
    elif file_path.endswith(".docx"):
        doc = docx.Document(file_path)
        text = "\n".join([para.text for para in doc.paragraphs])
    elif file_path.endswith(".txt"):
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
    return text

# AI-Based Question Generation
@app.route('/generate_questions', methods=['POST'])
def generate_questions():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    # Save file to /tmp directory for Cloud compatibility
    file_path = os.path.join(UPLOADS_DIR, file.filename)
    file.save(file_path)
    print(f"✅ File saved at: {file_path}")

    # Extract text
    extracted_text = extract_text_from_file(file_path)

    # Create a Gemini model object
    model = genai.GenerativeModel("gemini-1.5-pro-002")  # Choose an available model

    # Send text to Gemini AI for question generation
    prompt = f"Generate 10 multiple-choice questions from the following text:\n\n{extracted_text}\n\nEach question should have 1 correct answer and 3 incorrect answers."
    response = model.generate_content(prompt)

    # Parse AI Response
    questions = []
    for line in response.text.split("\n\n"):
        parts = line.split("\n")
        if len(parts) >= 5:
            questions.append({
                "question": parts[0],
                "correct_answer": parts[1].replace("✅ ", ""),
                "incorrect_options": [parts[2].replace("❌ ", ""), parts[3].replace("❌ ", ""), parts[4].replace("❌ ", "")]
            })

    return jsonify({"questions": questions})

# Store AI Questions in DB & Assign to Quiz
@app.route('/add_ai_questions', methods=['POST'])
def add_ai_questions():
    data = request.json
    print("🔍 Received Data:", data)  # Debugging
    
    quiz_id = data.get('quiz_id')
    questions = data.get('questions')

    if not quiz_id or not questions:
        return jsonify({"error": "Quiz ID and questions are required"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()

        for q in questions:
            print(f"✅ Adding Question: {q['question']}")  # Debugging
            cursor.execute('''
                INSERT INTO questions (quiz_id, question, option1, option2, option3, option4, correct_answer)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (quiz_id, q['question'], q['incorrect_options'][0], q['incorrect_options'][1], q['incorrect_options'][2], q['correct_answer'], q['correct_answer']))

        conn.commit()
    
    # Upload database to Cloud Storage after modifications
    upload_db()

    print("✅ All AI-generated questions added successfully!")
    return jsonify({"message": "AI-generated questions added successfully!"})

@app.route('/add_quiz', methods=['GET','POST'])
def add_quiz():
    data = request.json  # Get JSON data from request
    title = data.get('title')
    category = data.get('category')

    if not title or not category:
        return jsonify({"error": "Title and Category are required"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO quizzes (title, category) VALUES (?, ?)", (title, category))
        conn.commit()
    
    # Upload database to Cloud Storage after modifications
    upload_db()

    return jsonify({"message": "Quiz added successfully!"}), 201

@app.route('/move_to_next_question', methods=['POST'])
def move_to_next_question():
    data = request.json
    game_pin = data.get('game_pin')
    new_question_index = data.get('new_question_index')
    
    if not game_pin or new_question_index is None:
        return jsonify({"error": "Game PIN and new question index are required"}), 400
    
    try:
        with connect_db() as conn:
            cursor = conn.cursor()
            
            # Update the current question index for this game
            cursor.execute(
                "UPDATE game_sessions SET current_question_index = ? WHERE pin = ?", 
                (new_question_index, game_pin)
            )
            
            # Check if update was successful
            if cursor.rowcount == 0:
                return jsonify({"error": "Invalid game PIN"}), 404
                
            conn.commit()
            
        # Upload database to Cloud Storage after modifications
        upload_db()
        return jsonify({"success": True})
    except sqlite3.Error as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route('/check_current_question', methods=['GET'])
def check_current_question():
    game_pin = request.args.get('game_pin')
    
    if not game_pin:
        return jsonify({"error": "Game PIN is required"}), 400
    
    try:
        with connect_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT current_question_index FROM game_sessions WHERE pin = ?", 
                (game_pin,)
            )
            
            result = cursor.fetchone()
            if not result:
                return jsonify({"error": "Invalid game PIN"}), 404
                
            current_question_index = result[0] or 0
            
        return jsonify({"current_question_index": current_question_index})
    except sqlite3.Error as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route("/join_quiz", methods=["POST"])
def join_quiz():
    data = request.get_json()
    player_name = data.get("playerName")
    phone_number = data.get("phoneNumber")
    email_id = data.get("emailId")
    district = data.get("district")
    game_pin = data.get("gamePin")
    
    # Convert game_pin to string to ensure consistent key type
    game_pin_str = str(game_pin)
    
    print(f"DEBUG: Received join request from {player_name} for PIN {game_pin_str}")

    if not all([player_name, phone_number, email_id, district, game_pin]):
        return jsonify({"error": "All fields are required!"}), 400

    try:
        conn = connect_db()
        cursor = conn.cursor()

        # Check if the game PIN exists in the active game_sessions
        cursor.execute("SELECT * FROM game_sessions WHERE pin = ? AND status = 'active'", (game_pin,))
        game = cursor.fetchone()

        if not game:
            return jsonify({"error": "Invalid or inactive game PIN!"}), 400

        # Check if the started_for_all column exists in game_sessions table
        try:
            cursor.execute("SELECT started_for_all FROM game_sessions WHERE pin = ?", (game_pin,))
        except sqlite3.OperationalError:
            # Column doesn't exist, add it
            cursor.execute("ALTER TABLE game_sessions ADD COLUMN started_for_all INTEGER DEFAULT 0")
            conn.commit()

        # Check if the quiz has already started for all
        cursor.execute("SELECT started_for_all FROM game_sessions WHERE pin = ?", (game_pin,))
        started = cursor.fetchone()[0]

        if started == 1:
            # If quiz already started, add participant directly
            cursor.execute(
                "INSERT INTO participants (player_name, phone_number, email_id, district, game_pin) VALUES (?, ?, ?, ?, ?)",
                (player_name, phone_number, email_id, district, game_pin)
            )
            conn.commit()
        else:
            # Otherwise, add to waiting list - use string key
            if game_pin_str not in waiting_participants:
                waiting_participants[game_pin_str] = []
            
            from datetime import datetime
            waiting_participants[game_pin_str].append({
                'player_name': player_name,
                'phone_number': phone_number,
                'email_id': email_id,
                'district': district,
                'join_time': datetime.now().strftime("%H:%M:%S")
            })
            
            print(f"DEBUG: Added {player_name} to waiting list for PIN {game_pin_str}")
            print(f"DEBUG: Current waiting list for PIN {game_pin_str}: {waiting_participants.get(game_pin_str, [])}")

        conn.close()
        
        # Upload database to Cloud Storage after modifications
        upload_db()
        return jsonify({"success": True}), 200

    except sqlite3.Error as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route('/check_quiz_status', methods=['GET'])
def check_quiz_status():
    game_pin = request.args.get('game_pin')
    player_name = request.args.get('player_name')
    
    if not game_pin or not player_name:
        return jsonify({"error": "Game PIN and player name are required"}), 400
    
    # Check if this game has been started for all
    try:
        with connect_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT started_for_all FROM game_sessions WHERE pin = ?", (game_pin,))
            result = cursor.fetchone()
            
            if not result:
                return jsonify({"error": "Invalid game PIN"}), 404
            
            # Check if started for all
            if result[0] == 1:
                print(f"DEBUG: Quiz with PIN {game_pin} has started for all participants")
                return jsonify({"status": "started"})
            else:
                return jsonify({"status": "waiting"})
    except sqlite3.Error as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route('/get_waiting_participants', methods=['GET'])
def get_waiting_participants():
    game_pin = request.args.get('game_pin')
    if not game_pin:
        return jsonify({"error": "Game PIN is required"}), 400
    
    # Convert to string to ensure consistent key type
    game_pin_str = str(game_pin)
    
    # Return participants waiting for this game - use string key
    participants = waiting_participants.get(game_pin_str, [])
    print(f"DEBUG: Returning {len(participants)} waiting participants for PIN {game_pin_str}")
    return jsonify({"participants": participants})

@app.route('/start_quiz_for_all', methods=['POST'])
def start_quiz_for_all():
    data = request.get_json()
    game_pin = data.get('game_pin')
    
    if not game_pin:
        return jsonify({"error": "Game PIN is required"}), 400
    
    # Convert to string to ensure consistent key type
    game_pin_str = str(game_pin)
    
    print(f"DEBUG: Attempting to start quiz for all with PIN: {game_pin_str}")
    print(f"DEBUG: Current waiting participants: {waiting_participants}")
    print(f"DEBUG: Waiting for game PIN {game_pin_str}: {waiting_participants.get(game_pin_str, [])}")
    
    # Add all waiting participants to the database
    if game_pin_str in waiting_participants and waiting_participants[game_pin_str]:
        try:
            with connect_db() as conn:
                cursor = conn.cursor()
                
                # Mark this game as started for all
                cursor.execute("UPDATE game_sessions SET started_for_all = 1 WHERE pin = ?", (game_pin,))
                
                # Check if update worked
                cursor.execute("SELECT started_for_all FROM game_sessions WHERE pin = ?", (game_pin,))
                status = cursor.fetchone()
                print(f"DEBUG: After update, started_for_all = {status[0] if status else 'not found'}")
                
                # Make a copy of participants before we clear the list
                participants_copy = waiting_participants[game_pin_str].copy()
                
                # Insert all waiting participants
                for participant in participants_copy:
                    cursor.execute(
                        "INSERT INTO participants (player_name, phone_number, email_id, district, game_pin) VALUES (?, ?, ?, ?, ?)",
                        (participant['player_name'], participant['phone_number'], participant['email_id'], participant['district'], game_pin)
                    )
                
                conn.commit()
                
                participant_count = len(participants_copy)
                
                # Clear waiting list AFTER successful database insertion
                waiting_participants[game_pin_str] = []
                
                print(f"DEBUG: Successfully started quiz for {participant_count} participants")
                
                # Upload database to Cloud Storage after modifications
                upload_db()
                return jsonify({"success": True, "participants_count": participant_count})
        except sqlite3.Error as e:
            print(f"DEBUG: Database error when starting quiz: {str(e)}")
            return jsonify({"error": f"Database error: {str(e)}"}), 500
    
    print("DEBUG: No waiting participants found, or PIN not in dictionary")
    return jsonify({"success": True, "participants_count": 0})

@app.route("/add_question", methods=["POST"])
def add_question():
    data = request.get_json()

    # Extract fields
    class_level = data.get("class_level")
    subject = data.get("subject")
    book_name = data.get("book_name")
    chapter = data.get("chapter")
    question = data.get("question")
    option1 = data.get("option1")
    option2 = data.get("option2")
    option3 = data.get("option3")
    option4 = data.get("option4")
    correct_answer = data.get("correct_answer")
    quiz_id = data.get("quiz_id")  # Optional

    # Validate required fields
    if not all([class_level, subject, book_name, chapter, question, option1, option2, option3, option4, correct_answer]):
        return jsonify({"error": "All fields are required!"}), 400

    try:
        with connect_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO questions (
                    quiz_id, class_level, subject, book_name, chapter, question,
                    option1, option2, option3, option4, correct_answer
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                quiz_id, class_level, subject, book_name, chapter, question,
                option1, option2, option3, option4, correct_answer
            ))
            conn.commit()
        
        # Upload database to Cloud Storage after modifications
        upload_db()
        return jsonify({"success": "Question added successfully!"}), 200

    except sqlite3.IntegrityError as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route('/get_questions', methods=['POST'])
def get_questions():
    data = request.json
    game_pin = data.get('game_pin')

    if not game_pin:
        return jsonify({"error": "Game PIN is required!"}), 400

    try:
        with connect_db() as conn:
            cursor = conn.cursor()
            
            # Get quiz_id for the given game_pin
            cursor.execute("SELECT quiz_id FROM game_sessions WHERE pin = ? AND status = 'active'", (game_pin,))
            result = cursor.fetchone()

            if not result:
                return jsonify({"error": "Invalid or expired Game PIN!"}), 404

            quiz_id = result[0]

            # Fetch all questions for the quiz
            cursor.execute("""
                SELECT id, question, option1, option2, option3, option4 FROM questions 
                WHERE quiz_id = ? ORDER BY id
            """, (quiz_id,))
            questions = cursor.fetchall()

        if not questions:
            return jsonify({"error": "No questions found for this quiz!"}), 404

        questions_list = []
        for q in questions:
            questions_list.append({
                "question_id": q[0],
                "question": q[1],
                "options": [q[2], q[3], q[4], q[5]]
            })

        return jsonify({"success": True, "questions": questions_list})

    except sqlite3.Error as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route('/get_question_responses', methods=['POST'])
def get_question_responses():
    data = request.json
    game_pin = data.get('game_pin')
    question_id = data.get('question_id')
    
    if not game_pin or not question_id:
        return jsonify({"error": "Game PIN and question ID are required"}), 400
    
    try:
        with connect_db() as conn:
            cursor = conn.cursor()
            
            # Get the correct answer for this question
            cursor.execute(
                "SELECT correct_answer FROM questions WHERE id = ?", 
                (question_id,)
            )
            
            question_result = cursor.fetchone()
            if not question_result:
                return jsonify({"error": "Question not found"}), 404
                
            correct_answer = question_result[0]
            
            # Get all responses for this question
            cursor.execute(
                """
                SELECT participant, answer, is_correct 
                FROM responses 
                WHERE game_pin = ? AND question_id = ?
                """, 
                (game_pin, question_id)
            )
            
            responses = cursor.fetchall()
            
            response_list = [
                {
                    "participant": r[0],
                    "answer": r[1],
                    "is_correct": bool(r[2])
                }
                for r in responses
            ]
            
        return jsonify({
            "success": True,
            "correct_answer": correct_answer,
            "responses": response_list,
            "total_responses": len(response_list)
        })
    except sqlite3.Error as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route('/get_correct_answers', methods=['POST'])
def get_correct_answers():
    data = request.json
    question_ids = data.get('question_ids')
    
    if not question_ids:
        return jsonify({"error": "Question IDs are required"}), 400
    
    try:
        with connect_db() as conn:
            cursor = conn.cursor()
            
            correct_answers = []
            for question_id in question_ids:
                cursor.execute(
                    "SELECT correct_answer FROM questions WHERE id = ?", 
                    (question_id,)
                )
                
                result = cursor.fetchone()
                if result:
                    correct_answers.append(result[0])
                else:
                    correct_answers.append(None)
            
        return jsonify({
            "success": True,
            "correct_answers": correct_answers
        })
    except sqlite3.Error as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    

@app.route("/get_books_and_chapters")
def get_books_and_chapters():
    class_level = request.args.get("class_level")
    subject = request.args.get("subject")

    if not class_level or not subject:
        return jsonify({"books": [], "chapters": []})

    # Assuming you store questions with class, subject, book_name, chapter fields
    conn = sqlite3.connect("your_database.db")
    cursor = conn.cursor()

    # Get distinct books
    cursor.execute(
        "SELECT DISTINCT book_name FROM questions WHERE class = ? AND subject = ?",
        (class_level, subject)
    )
    books = [row[0] for row in cursor.fetchall()]

    # Get distinct chapters
    cursor.execute(
        "SELECT DISTINCT chapter FROM questions WHERE class = ? AND subject = ?",
        (class_level, subject)
    )
    chapters = [row[0] for row in cursor.fetchall()]

    conn.close()
    return jsonify({"books": books, "chapters": chapters})





@app.route("/fetch_filtered_questions", methods=["POST"])
def fetch_filtered_questions():
    data = request.get_json()
    class_level = data.get("class_level")
    subject = data.get("subject")
    book_name = data.get("book_name")
    chapter = data.get("chapter")  # ✅ Included chapter

    if not class_level or not subject or not book_name or not chapter:
        return jsonify({"error": "Missing parameters!"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, question, correct_answer FROM questions
            WHERE class_level = ? AND subject = ? AND book_name = ? AND chapter = ?
        """, (class_level, subject, book_name, chapter))
        
        questions = [{"id": row[0], "question": row[1], "correct_answer": row[2]} for row in cursor.fetchall()]

    return jsonify({"questions": questions})








@app.route("/generate_pre_post_quiz", methods=["POST"])
def generate_pre_post_quiz():
    try:
        data = request.get_json()
        class_level = data.get("class_level")
        subject = data.get("subject")
        book_name = data.get("book_name")
        chapter = data.get("chapter")

        if not class_level or not subject or not book_name or not chapter:
            return jsonify({"error": "Missing parameters!"}), 400

        with connect_db() as conn:
            cursor = conn.cursor()

            # Fetch available questions from DB
            cursor.execute("""
                SELECT id, question, option1, option2, option3, option4, correct_answer 
                FROM questions
                WHERE class_level = ? AND subject = ? AND book_name = ? AND chapter = ?
            """, (class_level, subject, book_name, chapter))

            questions = cursor.fetchall()

        if len(questions) < 20:
            return jsonify({"error": "Not enough questions to create two separate quizzes!"}), 400

        # Shuffle and split questions
        random.shuffle(questions)
        pre_quiz_questions = questions[:10]
        post_quiz_questions = questions[10:20]

        with connect_db() as conn:
            cursor = conn.cursor()

            # Insert Pre-Assessment Quiz
            cursor.execute("INSERT INTO quizzes (title, category) VALUES (?, ?)", 
                           (f"Pre-Assessment Quiz ({class_level}-{subject})", "Pre-Assessment"))
            pre_quiz_id = cursor.lastrowid

            for q in pre_quiz_questions:
                cursor.execute("""
                    INSERT INTO questions (quiz_id, class_level, subject, book_name, chapter, question, option1, option2, option3, option4, correct_answer)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (pre_quiz_id, class_level, subject, book_name, chapter, *q[1:]))

            # Insert Post-Assessment Quiz
            cursor.execute("INSERT INTO quizzes (title, category) VALUES (?, ?)", 
                           (f"Post-Assessment Quiz ({class_level}-{subject})", "Post-Assessment"))
            post_quiz_id = cursor.lastrowid

            for q in post_quiz_questions:
                cursor.execute("""
                    INSERT INTO questions (quiz_id, class_level, subject, book_name, chapter, question, option1, option2, option3, option4, correct_answer)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (post_quiz_id, class_level, subject, book_name, chapter, *q[1:]))

            conn.commit()

        return jsonify({
            "success": True,
            "pre_quiz_id": pre_quiz_id,
            "post_quiz_id": post_quiz_id
        })

    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500






















@app.route("/get_books", methods=["POST"])
def get_books():
    data = request.get_json()
    class_level = data.get("class_level")
    subject = data.get("subject")

    if not class_level or not subject:
        return jsonify({"error": "Class Level and Subject are required!"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT book_name FROM questions WHERE class_level = ? AND subject = ? AND book_name IS NOT NULL", 
                       (class_level, subject))
        books = [row[0] for row in cursor.fetchall()]

    return jsonify({"books": books})




@app.route("/get_chapters", methods=["POST"])
def get_chapters():
    data = request.get_json()
    class_level = data.get("class_level")
    subject = data.get("subject")
    book_name = data.get("book_name")

    if not class_level or not subject or not book_name:
        return jsonify({"error": "Class, Subject, and Book are required!"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT chapter FROM questions WHERE class_level = ? AND subject = ? AND book_name = ?", 
                       (class_level, subject, book_name))
        chapters = [row[0] for row in cursor.fetchall()]

    return jsonify({"chapters": chapters})





@app.route("/create_quiz_from_questions", methods=["POST"])
def create_quiz_from_questions():
    data = request.get_json()
    quiz_title = data.get("quizTitle")
    quiz_category = data.get("quizCategory")
    selected_questions = data.get("selectedQuestions")

    if not quiz_title or not quiz_category or not selected_questions:
        return jsonify({"error": "All fields are required!"}), 400

    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()

        # Insert new quiz
        cursor.execute("INSERT INTO quizzes (title, category) VALUES (?, ?)", (quiz_title, quiz_category))
        quiz_id = cursor.lastrowid  # Get the ID of the newly created quiz

        # Assign selected questions to this quiz
        for question_id in selected_questions:
            cursor.execute("UPDATE questions SET quiz_id = ? WHERE id = ?", (quiz_id, question_id))

        conn.commit()
        conn.close()

        return jsonify({"success": "Quiz created successfully!"}), 200

    except sqlite3.Error as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500








@app.route('/submit_answer', methods=['POST'])
def submit_answer():
    data = request.json
    game_pin = data.get('game_pin')
    question_id = data.get('question_id')
    participant = data.get('participant')
    answer = data.get('answer')

    if not all([game_pin, question_id, participant, answer]):
        return jsonify({"error": "All fields are required"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()
        # Fetch correct answer for validation
        cursor.execute("SELECT correct_answer FROM questions WHERE id = ?", (question_id,))
        correct_answer = cursor.fetchone()

        if not correct_answer:
            return jsonify({"error": "Invalid question"}), 404

        is_correct = 1 if answer == correct_answer[0] else 0

        # Store response
        cursor.execute("INSERT INTO responses (game_pin, question_id, participant, answer, is_correct) VALUES (?, ?, ?, ?, ?)",
                       (game_pin, question_id, participant, answer, is_correct))
        conn.commit()

    return jsonify({"message": "Answer submitted!", "is_correct": is_correct})




@app.route('/get_responses', methods=['POST'])
def get_responses():
    data = request.json
    game_pin = data.get('game_pin')

    if not game_pin:
        return jsonify({"error": "Game PIN is required"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT question_id, participant, answer, is_correct 
            FROM responses WHERE game_pin = ?
        """, (game_pin,))
        responses = cursor.fetchall()

    response_list = []
    for r in responses:
        response_list.append({
            "question_id": r[0],
            "participant": r[1],
            "answer": r[2],
            "is_correct": bool(r[3])
        })

    return jsonify({"responses": response_list})






@app.route('/get_scores', methods=['POST'])
def get_scores():
    data = request.json
    game_pin = data.get('game_pin')

    if not game_pin:
        return jsonify({"error": "Game PIN is required"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT participant, SUM(is_correct) as score 
            FROM responses WHERE game_pin = ? 
            GROUP BY participant ORDER BY score DESC
        """, (game_pin,))
        scores = cursor.fetchall()

    leaderboard = []
    for s in scores:
        leaderboard.append({
            "participant": s[0],
            "score": s[1]
        })

    return jsonify({"leaderboard": leaderboard})












@app.route('/get_quizzes', methods=['GET'])
def get_quizzes():
    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM quizzes")
        quizzes = cursor.fetchall()

    # Convert database result to JSON format
    quizzes_list = []
    for quiz in quizzes:
        quizzes_list.append({
            "id": quiz[0],
            "title": quiz[1],
            "category": quiz[2]
        })

    return jsonify(quizzes_list)



@app.route("/get_quiz_questions", methods=["POST"])  # ✅ Ensure POST method
def get_quiz_questions():
    data = request.get_json()
    quiz_id = data.get("quiz_id")

    if not quiz_id:
        return jsonify({"error": "Quiz ID is required!"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT question, option1, option2, option3, option4, correct_answer
            FROM questions
            WHERE quiz_id = ?
        """, (quiz_id,))
        questions = cursor.fetchall()

    if not questions:
        return jsonify({"error": "No questions found for this quiz!"}), 404

    question_list = [
        {
            "question": q[0],
            "options": [q[1], q[2], q[3], q[4]],
            "correct_answer": q[5]
        }
        for q in questions
    ]

    return jsonify({"questions": question_list})






import random
import random

@app.route('/start_quiz', methods=['POST'])
def start_quiz():
    data = request.json
    quiz_id = data.get('quiz_id')

    if not quiz_id:
        return jsonify({"error": "Quiz ID is required"}), 400

    # Generate a random 6-digit PIN
    game_pin = random.randint(100000, 999999)

    with connect_db() as conn:
        cursor = conn.cursor()
        
        # Ensure tables exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS game_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id INTEGER NOT NULL,
                pin INTEGER NOT NULL,
                status TEXT DEFAULT "active",
                FOREIGN KEY (quiz_id) REFERENCES quizzes(id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_pin INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                participant TEXT NOT NULL,
                answer TEXT NOT NULL,
                is_correct INTEGER NOT NULL
            )
        ''')
        conn.commit()

        # 🛑 **Fix: Insert the new game session into `game_sessions`**
        cursor.execute('''
            INSERT INTO game_sessions (quiz_id, pin, status) VALUES (?, ?, 'active')
        ''', (quiz_id, game_pin))
        conn.commit()  # ✅ Save changes to DB

    return jsonify({"message": "Quiz started!", "game_pin": game_pin})










@app.route('/leaderboard', methods=['POST'])
def leaderboard():
    data = request.json
    game_pin = data.get('game_pin')

    if not game_pin:
        return jsonify({"error": "Game PIN is required"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()

        # Fetch participant scores from the responses table
        cursor.execute('''
            SELECT participant, SUM(is_correct) as score
            FROM responses
            WHERE game_pin = ?
            GROUP BY participant
            ORDER BY score DESC
        ''', (game_pin,))
        leaderboard_data = cursor.fetchall()

    # Convert to JSON format
    leaderboard_list = [{"participant": row[0], "score": row[1]} for row in leaderboard_data]

    return jsonify({"leaderboard": leaderboard_list})




@app.route('/performance_analysis', methods=['POST'])
def performance_analysis():
    data = request.json
    game_pin = data.get('game_pin')

    if not game_pin:
        return jsonify({"error": "Game PIN is required"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()

        # Fetch participant scores
        cursor.execute('''
            SELECT participant, SUM(is_correct) as score
            FROM responses
            WHERE game_pin = ?
            GROUP BY participant
            ORDER BY score DESC
        ''', (game_pin,))
        scores_data = cursor.fetchall()

        participants = [row[0] for row in scores_data]
        scores = [row[1] for row in scores_data]

        # Fetch correct vs incorrect counts
        cursor.execute('''
            SELECT SUM(is_correct), COUNT(*) - SUM(is_correct)
            FROM responses WHERE game_pin = ?
        ''', (game_pin,))
        correct, incorrect = cursor.fetchone()

    return jsonify({
        "participants": participants,
        "scores": scores,
        "correct": correct,
        "incorrect": incorrect
    })



@app.route('/most_incorrect_questions', methods=['POST'])
def most_incorrect_questions():
    data = request.json
    game_pin = data.get('game_pin')

    if not game_pin:
        return jsonify({"error": "Game PIN is required"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()

        # Total participants who answered at least one question
        cursor.execute("""
            SELECT COUNT(DISTINCT participant) FROM responses WHERE game_pin = ?
        """, (game_pin,))
        total_participants = cursor.fetchone()[0] or 1  # Avoid division by zero

        # Fetch incorrect question statistics
        cursor.execute("""
            SELECT q.question, q.correct_answer, 
                   COUNT(*) as total_attempts,
                   SUM(CASE WHEN r.is_correct = 0 THEN 1 ELSE 0 END) as incorrect_attempts
            FROM responses r
            JOIN questions q ON r.question_id = q.id
            WHERE r.game_pin = ?
            GROUP BY q.id
            ORDER BY incorrect_attempts DESC
        """, (game_pin,))
        incorrect_questions = cursor.fetchall()

    incorrect_list = []
    for q in incorrect_questions:
        question_text, correct_answer, total_attempts, incorrect_attempts = q
        incorrect_percentage = (incorrect_attempts / total_attempts) * 100 if total_attempts > 0 else 0

        incorrect_list.append({
            "question": question_text,
            "correct_answer": correct_answer,
            "incorrect_percentage": f"{incorrect_percentage:.2f}%"
        })

    return jsonify({"incorrect_questions": incorrect_list})



@app.route('/quiz_analysis', methods=['POST'])
def quiz_analysis():
    data = request.json
    game_pin = data.get('game_pin')

    if not game_pin:
        return jsonify({"error": "Game PIN is required"}), 400

    with connect_db() as conn:
        cursor = conn.cursor()

        # Get total participants
        cursor.execute("""
            SELECT COUNT(DISTINCT participant) FROM responses WHERE game_pin = ?
        """, (game_pin,))
        total_participants = cursor.fetchone()[0] or 1  # Avoid division by zero

        # Get participant scores
        cursor.execute("""
            SELECT participant, SUM(is_correct) as score
            FROM responses WHERE game_pin = ?
            GROUP BY participant
        """, (game_pin,))
        scores = cursor.fetchall()

        # Calculate average score
        total_score = sum([s[1] for s in scores])
        average_score = total_score / total_participants if total_participants > 0 else 0

        # Calculate success rate (70% threshold)
        passing_threshold = 0.7  # 70% passing criteria
        max_questions = cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = (SELECT quiz_id FROM game_sessions WHERE pin = ?)", (game_pin,)).fetchone()[0]
        passing_score = max_questions * passing_threshold

        passed_participants = sum(1 for s in scores if s[1] >= passing_score)
        success_rate = (passed_participants / total_participants) * 100 if total_participants > 0 else 0

        # Determine difficulty level based on incorrect percentage
        cursor.execute("""
            SELECT SUM(is_correct), COUNT(*) 
            FROM responses WHERE game_pin = ?
        """, (game_pin,))
        correct, total_attempts = cursor.fetchone()
        incorrect_percentage = 100 - ((correct / total_attempts) * 100) if total_attempts > 0 else 100

        difficulty_level = "Easy" if incorrect_percentage < 30 else "Medium" if incorrect_percentage < 60 else "Hard"

    return jsonify({
        "success_rate": f"{success_rate:.2f}%",
        "average_score": f"{average_score:.2f}",
        "difficulty_level": difficulty_level
    })






@app.route('/compare_training', methods=['GET'])
def compare_training():
    pre_pin = request.args.get('pre_pin')
    post_pin = request.args.get('post_pin')

    if not pre_pin or not post_pin:
        return jsonify({"error": "Both Game PINs are required!"}), 400

    try:
        with connect_db() as conn:
            cursor = conn.cursor()

            # Get Quiz IDs for both Game PINs
            cursor.execute("SELECT quiz_id FROM game_sessions WHERE pin = ?", (pre_pin,))
            pre_quiz = cursor.fetchone()

            cursor.execute("SELECT quiz_id FROM game_sessions WHERE pin = ?", (post_pin,))
            post_quiz = cursor.fetchone()

            if not pre_quiz or not post_quiz:
                return jsonify({"error": "Invalid Game PIN(s)!"}), 404

            # Fetch Success Rate
            cursor.execute("SELECT SUM(is_correct), COUNT(*) FROM responses WHERE game_pin = ?", (pre_pin,))
            pre_correct, pre_total = cursor.fetchone()

            cursor.execute("SELECT SUM(is_correct), COUNT(*) FROM responses WHERE game_pin = ?", (post_pin,))
            post_correct, post_total = cursor.fetchone()

            pre_success_rate = (pre_correct / pre_total) * 100 if pre_total else 0
            post_success_rate = (post_correct / post_total) * 100 if post_total else 0

        return jsonify({
            "success": True,
            "success_rate": {"pre": pre_success_rate, "post": post_success_rate}
        })

    except sqlite3.Error as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500











# Dummy data (Replace with DB query)
quizzes = [
    {"id": "101", "title": "Math Test", "date": "2025-03-29", "time": "10:00 AM", "slot": 1},
    {"id": "102", "title": "Science Quiz", "date": "2025-03-28", "time": "2:00 PM", "slot": 2},
]

@app.route("/get_last_quizzes")
def get_last_quizzes():
    return jsonify(quizzes)

@app.route("/quiz_summary/<quiz_id>")
def quiz_summary(quiz_id):
    return f"<h1>Summary for Quiz {quiz_id}</h1>"  # Replace with actual summary page






@app.route("/add_new_question", methods=["POST"])
def add_new_question():
    data = request.get_json()

    class_level = data.get("class_level")
    subject = data.get("subject")
    book_name = data.get("book_name")
    chapter = data.get("chapter")
    question = data.get("question")
    option1 = data.get("option1")
    option2 = data.get("option2")
    option3 = data.get("option3")
    option4 = data.get("option4")
    correct_answer = data.get("correct_answer")

    if not all([class_level, subject, book_name, chapter, question, option1, option2, option3, option4, correct_answer]):
        return jsonify({"error": "All fields are required!"}), 400

    try:
        with connect_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO new_questions (
                    class_level, subject, book_name, chapter, question,
                    option1, option2, option3, option4, correct_answer
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (class_level, subject, book_name, chapter, question,
                  option1, option2, option3, option4, correct_answer))
            conn.commit()
        return jsonify({"success": "Question added successfully!"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500








































from flask import send_from_directory

@app.route('/')
def home():
    return send_from_directory('static', 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))  # Google App Engine uses port 8080
    app.run(host="0.0.0.0", port=port)
