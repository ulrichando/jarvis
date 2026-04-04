"""
Preapproved hosts for the WebFetchTool.

SECURITY WARNING: These preapproved domains are ONLY for WebFetch (GET requests only).
The sandbox system deliberately does NOT inherit this list for network restrictions,
as arbitrary network access (POST, uploads, etc.) to these domains could enable
data exfiltration.
"""
from __future__ import annotations


PREAPPROVED_HOSTS: frozenset[str] = frozenset([
    # JARVIS / AI reference
    "modelcontextprotocol.io",
    "docs.anthropic.com",
    "huggingface.co",

    # Top Programming Languages
    "docs.python.org",
    "en.cppreference.com",
    "docs.oracle.com",
    "learn.microsoft.com",
    "developer.mozilla.org",
    "go.dev",
    "pkg.go.dev",
    "www.php.net",
    "docs.swift.org",
    "kotlinlang.org",
    "ruby-doc.org",
    "doc.rust-lang.org",
    "www.typescriptlang.org",

    # Web & JavaScript Frameworks/Libraries
    "react.dev",
    "angular.io",
    "vuejs.org",
    "nextjs.org",
    "expressjs.com",
    "nodejs.org",
    "bun.sh",
    "jquery.com",
    "getbootstrap.com",
    "tailwindcss.com",
    "d3js.org",
    "threejs.org",
    "redux.js.org",
    "webpack.js.org",
    "jestjs.io",
    "reactrouter.com",

    # Python Frameworks & Libraries
    "docs.djangoproject.com",
    "flask.palletsprojects.com",
    "fastapi.tiangolo.com",
    "pandas.pydata.org",
    "numpy.org",
    "www.tensorflow.org",
    "pytorch.org",
    "scikit-learn.org",
    "matplotlib.org",
    "requests.readthedocs.io",
    "jupyter.org",

    # PHP Frameworks
    "laravel.com",
    "symfony.com",
    "wordpress.org",

    # Java Frameworks & Libraries
    "docs.spring.io",
    "hibernate.org",
    "tomcat.apache.org",
    "gradle.org",
    "maven.apache.org",

    # .NET & C# Frameworks
    "asp.net",
    "dotnet.microsoft.com",
    "nuget.org",
    "blazor.net",

    # Mobile Development
    "reactnative.dev",
    "docs.flutter.dev",
    "developer.apple.com",
    "developer.android.com",

    # Data Science & Machine Learning
    "keras.io",
    "spark.apache.org",
    "huggingface.co",
    "www.kaggle.com",

    # Databases
    "www.mongodb.com",
    "redis.io",
    "www.postgresql.org",
    "dev.mysql.com",
    "www.sqlite.org",
    "graphql.org",
    "prisma.io",

    # Cloud & DevOps
    "docs.aws.amazon.com",
    "cloud.google.com",
    "kubernetes.io",
    "www.docker.com",
    "www.terraform.io",
    "www.ansible.com",
    "vercel.com/docs",
    "docs.netlify.com",
    "devcenter.heroku.com",

    # Testing & Monitoring
    "cypress.io",
    "selenium.dev",

    # Game Development
    "docs.unity.com",
    "docs.unrealengine.com",

    # Other Essential Tools
    "git-scm.com",
    "nginx.org",
    "httpd.apache.org",
])

# Split into hostname-only and path-prefixed entries for O(1) lookups
_HOSTNAME_ONLY: set[str] = set()
_PATH_PREFIXES: dict[str, list[str]] = {}

for _entry in PREAPPROVED_HOSTS:
    _slash = _entry.find("/")
    if _slash == -1:
        _HOSTNAME_ONLY.add(_entry)
    else:
        _host = _entry[:_slash]
        _path = _entry[_slash:]
        _PATH_PREFIXES.setdefault(_host, []).append(_path)


def is_preapproved_host(hostname: str, pathname: str) -> bool:
    """Check if a hostname/pathname combination is preapproved."""
    if hostname in _HOSTNAME_ONLY:
        return True
    prefixes = _PATH_PREFIXES.get(hostname)
    if prefixes:
        for p in prefixes:
            if pathname == p or pathname.startswith(p + "/"):
                return True
    return False
