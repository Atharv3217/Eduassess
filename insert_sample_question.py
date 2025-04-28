import sqlite3

def insert_dummy_questions():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    class_levels = ['1', '2', '3', '4', '5']
    subjects = ['Math', 'Science', 'English']
    books = ['Book A', 'Book B']
    chapters = ['Chapter 1', 'Chapter 2', 'Chapter 3']

    question_count = 0

    for class_level in class_levels:
        for subject in subjects:
            for book in books:
                for chapter in chapters:
                    if question_count >= 50:
                        break

                    question_text = f"What is the answer to question {question_count + 1}?"
                    option1 = f"Option A for Q{question_count + 1}"
                    option2 = f"Option B for Q{question_count + 1}"
                    option3 = f"Option C for Q{question_count + 1}"
                    option4 = f"Option D for Q{question_count + 1}"
                    correct_answer = option1  # Just set Option A as the answer

                    cursor.execute('''
                        INSERT INTO new_questions (
                            class_level, subject, book_name, chapter,
                            question, option1, option2, option3, option4, correct_answer
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        class_level, subject, book, chapter,
                        question_text, option1, option2, option3, option4, correct_answer
                    ))

                    question_count += 1

    conn.commit()
    conn.close()
    print(f"✅ {question_count} dummy questions inserted into 'new_questions' table.")

insert_dummy_questions()
