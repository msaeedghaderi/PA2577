# All At Once Clone Detector

## Start and deploy

Download the QualitasCorpus manually: https://bthse-my.sharepoint.com/:u:/g/personal/msv_bth_se/ETA72qrzdvtAkGqaXucVgVsB4obGzc1io_3oFPhO6qPgXg?e=KjhQQr 
Unzip this archive and put the two tar-files in BigDataAnalytics/Containers/CorpusGetter

run the following commands in terminal

docker build -t corpusgetter Containers/CorpusGetter
docker volume create qc-volume
docker run -it -v qc-volume:/QualitasCorpus -v ./Containers/CorpusGetter:/Download --name qc-getter corpusgetter ./qc-get.sh INSTALL

docker compose -f all-at-once.yaml build
