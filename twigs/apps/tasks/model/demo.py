"""Small, original syllabus used by demo mode and the import example."""

DEMO_SYLLABUS = {
    "version": 1,
    "id": "demo:learning-to-learn",
    "title": "Learning to learn",
    "description": "Build a sustainable practice: choose a question, study actively, and demonstrate what you understand.",
    "ascii_piece": "seed-germination",
    "ascii": "    .  /\n     \\|/\n   --(*)--\n     /|\\\n      |\n    __|__\n   /_____\\",
    "mermaid": "flowchart LR\n  Q[Choose a question] --> S[Study actively]\n  S --> P[Practise]\n  P --> E[Explain and reflect]\n  E --> Q",
    "resources": [
        {"id": "demo:study-guide", "title": "An active study notebook", "type": "book", "textbook": True,
         "chapters": [{"id": "questions", "title": "Ask a useful question"}, {"id": "retrieval", "title": "Recall before rereading"}]},
        {"id": "demo:video", "title": "An introductory lecture of your choice", "type": "video"}
    ],
    "modules": [
        {"id": "orientation", "title": "Find your question", "kind": "module", "children": [
            {"id": "question-reading", "title": "Read: asking useful questions", "kind": "read", "resource": "demo:study-guide", "chapter": "questions"},
            {"id": "question-exercise", "title": "Write one question you want to answer", "kind": "exercise", "notes": "Choose a question small enough to investigate in one week."}
        ]},
        {"id": "practice", "title": "Study, recall, and practise", "kind": "module", "requires": ["orientation"], "children": [
            {"id": "lecture", "title": "Watch an introductory lecture", "kind": "video", "resource": "demo:video"},
            {"id": "retrieval-reading", "title": "Read: recall before rereading", "kind": "read", "resource": "demo:study-guide", "chapter": "retrieval"},
            {"id": "retrieval-exercise", "title": "Explain the key idea without your notes", "kind": "exercise"},
            {"id": "extra", "title": "Find a second perspective", "kind": "article", "optional": True}
        ]},
        {"id": "demonstrate", "title": "Make something with what you learned", "kind": "project", "requires": ["practice"],
         "notes": "Write a short explanation, solve a fresh problem, or build a small example. Record the result as skill evidence."}
    ],
    "skills": [
        {"id": "question-framing", "title": "Frame a useful learning question", "criteria": ["question-reading", "question-exercise"]},
        {"id": "active-recall", "title": "Use active recall", "criteria": ["retrieval-reading", "retrieval-exercise", "demonstrate"]}
    ]
}
