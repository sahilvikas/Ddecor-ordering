from setuptools import setup, find_packages

setup(
    name="ddecor_ordering",
    version="0.1.0",
    description="Automated DDécor portal ordering via Playwright",
    author="CCP",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=[
        "playwright>=1.40.0",
    ],
)
