FROM python:3.7.3
RUN mkdir /app
COPY requirements.txt /app
RUN pip install -r /app/requirements.txt
ADD . /app
ENTRYPOINT ["python", "/app/test.py"]