# All At Once Clone Detector

Minimal setup to build images, load the Qualitas Corpus (QC), and run.

## Quick Start

1) **Download Qualitas Corpus**  
Place the two `.tar` files from the Qualitas Corpus into:
```
Containers/CorpusGetter/
```

2) **Build images**
```bash
docker build -t cljdetector Containers/cljdetector
docker build -t monitor-tool Containers/MonitorTool
docker build -t corpusgetter Containers/CorpusGetter
```

3) **Create volume for QC**
```bash
docker volume create qc-volume
# docker volume create monitor-data
```

4) **Populate the volume**
```bash
docker run -it   -v qc-volume:/QualitasCorpus   -v ./Containers/CorpusGetter:/Download   --name qc-getter   corpusgetter   ./qc-get.sh INSTALL
```

5) **Run**
```bash
docker compose -f all-at-once.yaml up --build
```

## Stop / Clean
```bash
docker compose -f all-at-once.yaml down
# Optional (deletes stored corpus):
docker volume rm qc-volume
```