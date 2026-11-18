# The stand-in for Meta and the phones. Separate from the product: it shares nothing with it but the network.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY metasim ./metasim
RUN useradd --system --uid 10001 sim && mkdir /data && chown sim /data
USER sim
ENV SIM_HOST=0.0.0.0 SIM_PORT=9100
EXPOSE 9100
CMD ["python", "-m", "metasim"]
