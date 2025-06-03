from setuptools import setup, find_namespace_packages


def get_description():
    return "Publishing tools for Amazon machine Images(AMI)"


def get_long_description():
    with open("README.md") as f:
        text = f.read()

    # Long description is everything after README's initial heading
    idx = text.find("\n\n")
    return text[idx:]


def get_requirements():
    with open("requirements.in") as f:
        return f.read().splitlines()


setup(
    name="pubtools-adc",
    version="2.4.2",
    packages=find_namespace_packages(where="src"),
    package_dir={"": "src"},
    url="https://github.com/JAVGan/pubtools-adc",
    license="GNU General Public License",
    description=get_description(),
    long_description=get_long_description(),
    long_description_content_type="text/markdown",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Programming Language :: Python :: 3",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    install_requires=get_requirements(),
    python_requires=">=3.6",
    entry_points={
        "console_scripts": [
            "pubtools-adc-push = pubtools._adc.tasks.push:entry_point",
        ]
    },
    project_urls={
        "Documentation": "https://release-engineering.github.io/pubtools-ami/",
        "Changelog": "https://github.com/JAVGan/pubtools-adc/blob/master/CHANGELOG.md",
    },
)
