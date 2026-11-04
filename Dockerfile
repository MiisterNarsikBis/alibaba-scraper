# Base image pinned by digest (the tag can move under us). Refresh deliberately, see CHANGES.md.
FROM chetan1111/botasaurus:chrome-151@sha256:0b94abc9c411b171ba4bae3f447c3746c1d54f7f2cebe397fa999c650a917fae

ENV PYTHONUNBUFFERED=1
# Never open debug prompts/devtools on crash.
ENV ENV=production

COPY requirements.txt .

RUN python -m pip install -r requirements.txt

RUN mkdir app
WORKDIR /app
COPY . /app

EXPOSE 8000

# The image ships real Google Chrome; browser.py starts its own Xvfb display
# for the one endpoint that needs a browser (/suppliers/search).
CMD ["python", "run.py"]
