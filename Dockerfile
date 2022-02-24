# syntax=docker/dockerfile:1

FROM python:3.9.5

WORKDIR /learn

COPY requirements.txt requirements.txt 

RUN pip3 install -r requirements.txt

COPY . . 

CMD ["python3","Untitled.py"]