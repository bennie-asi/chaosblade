FROM scratch
COPY busybox /bin/busybox
COPY busybox /bin/cp
COPY chaosblade /opt/chaosblade
WORKDIR /opt/chaosblade
ENTRYPOINT ["/bin/busybox", "tail", "-f", "/dev/null"]
