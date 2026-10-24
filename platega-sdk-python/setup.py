"""
Platega Python SDK Setup
"""

from setuptools import setup, find_packages

with open('README.md', 'r', encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='platega',
    version='1.2.0',
    author='Platega',
    author_email='support@platega.io',
    description='Python SDK for Platega.io payment system',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/platega/platega-python-sdk',
    project_urls={
        'Documentation': 'https://docs.platega.io/',
        'Bug Reports': 'https://github.com/platega/platega-python-sdk/issues',
    },
    packages=find_packages(),
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Office/Business :: Financial :: Point-Of-Sale',
    ],
    python_requires='>=3.7',
    install_requires=[],  # No dependencies - uses only stdlib
    extras_require={
        'dev': ['pytest', 'pytest-cov', 'black', 'mypy'],
    },
    keywords='platega payment gateway sdk api sbp cards crypto',
)
