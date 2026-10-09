"""Canonical skill alias map.

Maps common abbreviations / variants -> canonical skill name
as stored in skills_dataset.json (all lowercase).
"""

ALIASES = {
    # JavaScript ecosystem
    "js": "javascript",
    "ts": "typescript",
    "reactjs": "react",
    "react js": "react",
    "nextjs": "next.js",
    "next js": "next.js",
    "vuejs": "vue",
    "vue js": "vue",
    "nodejs": "node.js",
    "node js": "node.js",
    "node": "node.js",
    "expressjs": "express",
    "express js": "express",
    "nestjs": "nest.js",
    "nest js": "nest.js",
    "tailwind": "tailwind css",
    "tailwindcss": "tailwind css",
    "rest": "rest api",
    "restapi": "rest api",

    # Python / data
    "py": "python",
    "python3": "python",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "tf": "tensorflow",
    "torch": "pytorch",
    "hf": "hugging face",
    "hf transformers": "transformers",
    "cv": "computer vision",
    "nlproc": "natural language processing",
    "ML": "machine learning",
    "ml": "machine learning",
    "dl": "deep learning",
    "ds": "data science",
    "eda": "exploratory data analysis",
    "ab testing": "a/b testing",
    "a/b test": "a/b testing",

    # Cloud / DevOps
    "k8s": "kubernetes",
    "k8": "kubernetes",
    "amazon web service": "amazon web services",
    "gcp": "google cloud",
    "google cloud platform": "google cloud",
    "gcb": "google cloud",
    "ec2": "aws ec2",
    "s3": "aws s3",
    "lambda": "aws lambda",
    "rds": "aws rds",
    "ci cd": "ci/cd",
    "cicd": "ci/cd",
    "ci-cd": "ci/cd",
    "gh actions": "github actions",
    "elk": "elk stack",
    "tfm": "terraform",

    # Databases
    "postgres": "postgresql",
    "postgress": "postgresql",
    "mongo": "mongodb",
    "es": "elasticsearch",
    "elastic": "elasticsearch",
    "sql server": "sql",
    "no sql": "nosql",

    # Tools
    "vscode": "vs code",
    "vs code": "vs code",
    "visual studio code": "vs code",
    "jupyter": "jupyter notebook",
    "colab": "google colab",
    "gh": "github",
    "gl": "gitlab",

    # Languages
    "c plus plus": "c++",
    "cpp": "c++",
    "c sharp": "c#",
    "csharp": "c#",
    "golang": "go",
    "rb": "ruby",

    # Mobile
    "rn": "react native",
    "react-native": "react native",

    # Other normalizations
    "springboot": "spring boot",
    "spring": "spring boot",
    "dotnet": "asp.net",
    ".net": "asp.net",
}


def normalize_skill(skill: str) -> str:
    """Normalize a free-form skill string to its canonical form."""
    if not skill:
        return ""
    key = skill.lower().strip().replace("-", " ").replace("_", " ")
    key = " ".join(key.split())  # collapse whitespace
    # direct alias lookup (on collapsed key and raw lower)
    raw = skill.lower().strip()
    if raw in ALIASES:
        return ALIASES[raw]
    if key in ALIASES:
        return ALIASES[key]
    return key


def normalize_skill_list(skills) -> list:
    """Normalize + dedupe a list of skills, preserving order."""
    seen = set()
    out = []
    for s in skills or []:
        n = normalize_skill(s)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out
