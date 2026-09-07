# Copyright 2025 The ChaosBlade Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM scratch
COPY image-root/busybox /bin/sh
COPY image-root/busybox /bin/test
COPY image-root/busybox /bin/mkdir
COPY image-root/busybox /bin/tar
COPY image-root/busybox /bin/date
COPY image-root/busybox /bin/head
COPY image-root/busybox /bin/cksum
COPY image-root/busybox /bin/sed
COPY image-root/busybox /bin/grep
COPY image-root/busybox /bin/tail
COPY image-root/busybox /bin/awk
COPY image-root/busybox /bin/which
COPY image-root/busybox /bin/chmod
COPY image-root/busybox /bin/rm
COPY image-root/busybox /bin/ls
COPY image-root/busybox /bin/cat
COPY image-root/cksum /bin/cksum
COPY image-root/java /opt/java
COPY image-root/glibc /usr/lib/x86_64-linux-gnu
COPY image-root/ld-linux-x86-64.so.2 /lib64/ld-linux-x86-64.so.2
COPY image-root/java-config /etc/java-17-openjdk
COPY image-root/java-certs /etc/ssl/certs/java
COPY image-root/etc /etc
COPY image-root/tmp /tmp
COPY target/datasource-pool-app-1.0.0.jar /app/app.jar
ENTRYPOINT ["/opt/java/bin/java", "-jar", "/app/app.jar"]
