FROM public.ecr.aws/lambda/python:3.13-arm64

WORKDIR /var/task

COPY requirements.txt .

RUN pip install --upgrade pip 
RUN pip install -r requirements.txt -t /opt/python
