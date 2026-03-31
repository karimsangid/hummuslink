"""HummusLink setup configuration."""

from setuptools import setup, find_packages

setup(
    name="hummuslink",
    version="0.1.0",
    author="Hummus Development LLC",
    author_email="karimsangid@gmail.com",
    description="Cross-platform sync bridge between Windows and iPhone",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/karimsangid/hummuslink",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "fastapi>=0.110.0",
        "uvicorn[standard]>=0.29.0",
        "websockets>=12.0",
        "pyperclip>=1.8.0",
        "pystray>=0.19.0",
        "Pillow>=10.3.0",
        "qrcode>=7.4.0",
        "zeroconf>=0.131.0",
        "python-multipart>=0.0.9",
        "aiofiles>=23.2.0",
    ],
    entry_points={
        "console_scripts": [
            "hummuslink=main:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: Microsoft :: Windows",
    ],
    license="MIT",
)
