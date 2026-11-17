"""Per-role rules.

basic        - the BASIC requirements. Each requirement is a list of acceptable alternatives; finding ANY
               ONE of them anywhere in the resume satisfies that requirement (e.g. ["Python","Java",...]).
good_to_have - additional scored skills from role_requirements.json; they are not required individually.
"""
import json
from pathlib import Path

_JSON = Path(__file__).resolve().parent.parent / "role_requirements.json"

RULES = {
    "AI Engineer Intern": [["Python"], ["Artificial Intelligence"], ["Large Language Models"], ["LLM APIs"],
        ["Prompt Engineering"], ["Retrieval-Augmented Generation"], ["Vector Databases"], ["Semantic Search", "Embeddings"]],
    "AI Video Creation Intern": [["AI Video Generation", "Text-to-Video", "Image-to-Video"], ["Generative AI"],
        ["Video Editing"], ["Runway", "Kling AI", "Pika", "Google Flow", "Canva", "ElevenLabs"]],
    "Automation Intern": [["Python", "Scripting"], ["Automation", "Workflow Automation"],
        ["API Integration", "API"], ["Webhooks"]],
    "Backend Developer Intern": [["Python", "Java", "JavaScript", "Node.js", "PHP"], ["REST APIs"],
        ["SQL", "PostgreSQL", "MySQL", "MongoDB", "Databases"], ["OOP"], ["Data Structures"]],
    "Frontend Developer Intern": [["HTML"], ["CSS"], ["JavaScript"],
        ["React.js", "Angular", "Vue.js", "Lovable"], ["Responsive Design"], ["DOM"]],
    "Full Stack Developer Intern": [["HTML"], ["CSS"], ["JavaScript"], ["React.js", "Angular", "Vue.js"],
        ["Node.js", "Python", "Java", "PHP"], ["REST APIs"], ["SQL", "MongoDB"], ["CRUD"]],
    "Graphic Design Intern": [["Canva", "Adobe Photoshop", "Adobe Illustrator", "Figma"],
        ["Poster", "Logo", "Banner"], ["Social Media"]],
    "HR Intern": [["Recruitment", "Talent Acquisition"], ["Resume", "Screening"], ["Candidate Sourcing"],
        ["Interview Coordination"], ["MS Excel", "Google Sheets"], ["MS Office"]],
    "Prompt Engineering Intern": [["Prompt Engineering", "Prompt Design", "Prompt Optimization"],
        ["Generative AI"], ["LLMs"], ["ChatGPT", "Claude", "Google Gemini"], ["Token Optimization"]],
    "Python Developer Intern": [["Python"], ["OOP"], ["Data Structures", "Algorithms"], ["Functions"],
        ["Exception Handling"], ["File Handling"]],
    "Social Media Intern": [["Instagram", "Facebook", "LinkedIn", "YouTube"], ["Content Creation"], ["Canva"],
        ["Copywriting"], ["Social Media Marketing", "Digital Marketing"], ["Reels", "Shorts"], ["Video Editing"]],
    "Software Testing Intern": [["Manual Testing", "Software Testing"], ["Test Cases", "Test Scenarios"],
        ["Bug Reporting", "Bug Tracking"], ["STLC"], ["SDLC"]],
    "Content Creation Intern": [["Content Writing", "Creative Writing"], ["Copywriting"], ["Blog", "Article"],
        ["Script"], ["Social Media Content"], ["Storytelling"]],
    "Sales & Marketing Intern": [["Sales"], ["Lead Generation", "Business Development"], ["Digital Marketing"],
        ["Market Research"], ["Customer Relationship Management"], ["Cold Calling", "Cold Outreach"],
        ["Email Marketing"]],
    "Founder's Associate Intern": [["Business Development"], ["Market Research"], ["Competitor Analysis"],
        ["Business Strategy"], ["Business Analysis"], ["Excel", "Google Sheets"], ["PowerPoint", "Google Slides"],
        ["Project Management"], ["Communication"], ["Problem Solving"]],
    "DevOps Intern": [["Linux"], ["Git", "GitHub"], ["Docker"], ["CI/CD", "Continuous Integration"],
        ["AWS", "Azure", "Google Cloud", "GCP"], ["Shell Scripting", "Bash"], ["Networking Basics", "Networking"],
        ["Python"], ["Deployment"], ["Kubernetes"]],
    "Game Developer Intern": [["Unity", "Unreal Engine"], ["C#", "C++"], ["Game Development"], ["Game Design"],
        ["2D Game Development", "3D Game Development"], ["Gameplay Programming"], ["OOP", "Object-Oriented Programming"]],
    "Curriculum Developer Intern": [["Curriculum Development"], ["Content Development"], ["Instructional Design"],
        ["Lesson Planning"], ["Educational Content"], ["Research"], ["Learning Objectives"], ["Content Writing"],
        ["Subject Knowledge"], ["PowerPoint", "Google Slides"]],
}


# Resumes for these roles must show India (the word "India" or a +91 phone number), otherwise REJECT.
INDIA_ONLY_ROLES = {
    "Graphic Design Intern", "Social Media Intern", "Content Creation Intern",
    "Sales & Marketing Intern", "AI Video Creation Intern", "Prompt Engineering Intern",
}


# For EVERY role: a resume showing one of these countries is REJECTED. A country is "shown" when the resume
# contains one of its words (case-insensitive) or a phone number starting with one of its dialling codes.
BLOCKED_COUNTRIES = {
    "Pakistan": {"words": ["pakistan", "pakistani"], "phone_codes": ["92"]},
    "Bangladesh": {"words": ["bangladesh", "bangladeshi"], "phone_codes": ["880"]},
}


def load_roles():
    data = json.loads(_JSON.read_text(encoding="utf-8"))
    roles = {}
    for name, d in data.items():
        roles[name] = {"essential": d["essential"], "good_to_have": d["good_to_have"],
                       "note": d.get("note", ""), "basic": RULES[name],
                       "india_only": name in INDIA_ONLY_ROLES}
    return roles
