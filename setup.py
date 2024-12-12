from setuptools import setup, find_packages

setup(
    name="new_explain",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "alembic",
        "google-auth",
        "google-auth-oauthlib",
        "PyJWT",
    ],
)
