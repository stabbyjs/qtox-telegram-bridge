FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Системные зависимости для сборки c-toxcore и py-toxcore-c.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git pkg-config ca-certificates \
        libsodium-dev libopus-dev libvpx-dev \
    && rm -rf /var/lib/apt/lists/*

# Сборка и установка libtoxcore (TokTok/c-toxcore).
# --recurse-submodules обязателен: без сабмодуля third_party/cmp cmake падает
# с "No SOURCES given to target: toxcore_shared".
RUN git clone --depth 1 --recurse-submodules --shallow-submodules \
        https://github.com/TokTok/c-toxcore.git /tmp/c-toxcore \
    && cmake -S /tmp/c-toxcore -B /tmp/build \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DBOOTSTRAP_DAEMON=OFF \
        -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/build --target install -j"$(nproc)" \
    && ldconfig \
    && rm -rf /tmp/c-toxcore /tmp/build

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY docker/pytox-setup.py /tmp/pytox-setup.py

# py-toxcore-c ставится из git (на PyPI устаревший). setup.py ожидает уже
# сгенерированные .c, которых нет в репозитории, - генерируем их Cython вручную.
# toxav (звонки) исключён патч-setup'ом: не нужен и не собирается покомпонентно.
RUN pip install --no-cache-dir cython \
    && git clone --depth 1 https://github.com/TokTok/py-toxcore-c.git /tmp/pytox \
    && cp /tmp/pytox-setup.py /tmp/pytox/setup.py \
    && cython -3 \
        /tmp/pytox/pytox/toxcore/tox.pyx \
        /tmp/pytox/pytox/toxencryptsave/toxencryptsave.pyx \
    && pip install --no-cache-dir /tmp/pytox \
    && rm -rf /tmp/pytox \
    && pip install --no-cache-dir .

VOLUME ["/data"]
ENV DB_PATH=/data/bridge.db \
    TMP_DIR=/data/tmp \
    TOX_PROFILE_PATH=/data/profile.tox

CMD ["python", "-m", "bridge"]
