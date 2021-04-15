FROM python:3.9

RUN curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py | python -
RUN /root/.poetry/bin/poetry config virtualenvs.in-project true

COPY . /app
WORKDIR /app
RUN /root/.poetry/bin/poetry install
ENV FLASK_APP=space_gantt/app.py

EXPOSE 5000
CMD ["/app/.venv/bin/flask", "run", "--host", "0.0.0.0"]
