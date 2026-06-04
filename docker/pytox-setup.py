# Патч setup.py для py-toxcore-c: собираем только toxcore + toxencryptsave.
# Расширение toxav (аудио/видео-звонки) исключено - мост его не использует,
# а его .pyx не компилируется при покомпонентной cython-сборке.
from setuptools import Extension
from setuptools import setup

libraries = [
    "toxcore",
]
cflags = [
    "-funsigned-char",
]

setup(
    name="py-toxcore-c",
    version="0.2.20",
    description="Python binding for Tox (toxcore + toxencryptsave only)",
    author="Iphigenia Df",
    author_email="iphydf@gmail.com",
    url="http://github.com/TokTok/py-toxcore-c",
    license="GPL",
    py_modules=["pytox.common"],
    ext_modules=[
        Extension(
            "pytox.toxcore.tox",
            ["pytox/toxcore/tox.c"],
            extra_compile_args=cflags,
            libraries=libraries,
        ),
        Extension(
            "pytox.toxencryptsave.toxencryptsave",
            ["pytox/toxencryptsave/toxencryptsave.c"],
            extra_compile_args=cflags,
            libraries=libraries,
        ),
    ],
)
