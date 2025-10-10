# Clone Detector

## Start and deploy

Download the QualitasCorpus manually: https://bthse-my.sharepoint.com/:u:/g/personal/msv_bth_se/ETA72qrzdvtAkGqaXucVgVsB4obGzc1io_3oFPhO6qPgXg?e=KjhQQr 
Unzip this archive and put the two tar-files in BigDataAnalytics/Containers/CorpusGetter

run the following commands in terminal

docker build -t csgenerator Containers/CodeStreamGenerator
docker build -t csconsumer  Containers/CodeStreamConsumer
docker build -t corpusgetter Containers/CorpusGetter
docker volume create qc-volume
docker run -it -v qc-volume:/QualitasCorpus -v ./Containers/CorpusGetter:/Download --name qc-getter corpusgetter ./qc-get.sh INSTALL
docker compose -f stream-of-code.yaml up --build

# Clone Detector

Minimal setup to build images, load the Qualitas Corpus (QC), and run.

## Quick Start

1) **Download Qualitas Corpus**  
Place the two `.tar` files from the Qualitas Corpus into:
```
Containers/CorpusGetter/
```

2) **Build images**
```bash
docker build -t csgenerator Containers/CodeStreamGenerator
docker build -t csconsumer  Containers/CodeStreamConsumer
docker build -t corpusgetter Containers/CorpusGetter
```

3) **Create volume for QC**
```bash
docker volume create qc-volume
```

4) **Populate the volume**
```bash
docker run -it   -v qc-volume:/QualitasCorpus   -v ./Containers/CorpusGetter:/Download   --name qc-getter   corpusgetter   ./qc-get.sh INSTALL
```

5) **Run**
```bash
docker compose -f stream-of-code.yaml up --build
```

## Stop / Clean
```bash
docker compose -f stream-of-code.yaml down
# Optional (deletes stored corpus):
docker volume rm qc-volume
```