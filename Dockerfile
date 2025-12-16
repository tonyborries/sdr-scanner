FROM python:3.12

ENV PYTHONUNBUFFERED 1
RUN apt-get update
RUN apt-get install -y python3-dev build-essential 

# GNU Radio Deps
RUN apt-get install -y cmake libboost1.83-all-dev soapysdr-module-all soapysdr-module-rtlsdr rtl-sdr libsoapysdr-dev librtlsdr-dev libvolk-dev pybind11-dev libspdlog-dev libiio-dev python3-numpy-dev
RUN pip3 install --no-cache-dir --upgrade pip
RUN pip3 install packaging pygccxml PyYAML mako numpy

# debug tools
RUN apt-get install -y less soapysdr-tools

###
# Build gnuradio

WORKDIR /opt/
ADD https://github.com/gnuradio/gnuradio/archive/refs/tags/v3.10.12.0.tar.gz /opt/
# COPY v3.10.12.0.tar.gz /opt/
RUN ["/usr/bin/tar", "-xzf", "/opt/v3.10.12.0.tar.gz"]
RUN mv gnuradio-3.10.12.0 gnuradio
WORKDIR /opt/gnuradio/build
RUN cmake -DCMAKE_BUILD_TYPE=release -DENABLE_PERFORMANCE_COUNTERS=false -DENABLE_GR_QTGUI=OFF ../
RUN make
RUN make install
RUN ldconfig

###
# Python Deps

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python3", "cli_scan.py", "-c", "sdrscan.yaml"]

