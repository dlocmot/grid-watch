# Python 3.12+ es obligatorio: growattServer 2.x usa sintaxis PEP 695.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY grid_watch/ ./grid_watch/

RUN pip install --no-cache-dir . \
 && useradd --system --no-create-home grid-watch \
 && mkdir -p /var/lib/grid-watch /etc/grid-watch \
 && chown grid-watch:grid-watch /var/lib/grid-watch

USER grid-watch

# El estado (cola de alertas pendientes incluida) tiene que sobrevivir a
# recreaciones del contenedor, o un reinicio perdería avisos sin entregar.
VOLUME ["/var/lib/grid-watch"]

ENTRYPOINT ["grid-watch"]
CMD ["--config", "/etc/grid-watch/config.toml"]
