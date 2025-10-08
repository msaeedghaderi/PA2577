# Clone detector

## Start and deploy

docker build -t csgenerator Containers/CodeStreamGenerator
docker build -t csconsumer  Containers/CodeStreamConsumer
docker build -t corpusgetter Containers/CorpusGetter
docker volume create qc-volume
docker run -it -v qc-volume:/QualitasCorpus -v ./Containers/CorpusGetter:/Download --name qc-getter corpusgetter ./qc-get.sh INSTALL
docker compose -f stream-of-code.yaml down
