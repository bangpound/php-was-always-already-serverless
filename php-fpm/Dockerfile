# Inspired from https://github.com/araines/serverless-php by Andy Raines

# AWS Lambda uses this specific version
FROM amazonlinux:2017.03

ARG PHP_VERSION

# Lambda is based on 2017.03
# * dont' grab the latest revisions of development packages.
RUN yum --releasever=2017.03 install \
    autoconf \
    automake \
    libtool \
    bison \
    re2c \
    libxml2-devel \
    openssl-devel \
    libpng-devel \
    libjpeg-devel \
    curl-devel \
    # The pecl command uses find
    findutils \
    php-pear \
    # C++ & ICU are required by INTL
    libicu \
    libicu-devel \
    c++ \
    gcc-c++ -y

RUN curl -sL https://github.com/php/php-src/archive/$PHP_VERSION.tar.gz | tar -zxv

WORKDIR /php-src-$PHP_VERSION

RUN ./buildconf --force

RUN ./configure \
    # make sure invalid --configure-flags are fatal errors intead of just warnings
    --enable-option-checking=fatal \
    --enable-static=yes \
    --enable-shared=no \
    --disable-cgi \
    --enable-fpm \
    --disable-cli \
    --disable-phpdbg \
    # --enable-ftp is included here because ftp_ssl_connect() needs ftp to be compiled statically (see https://github.com/docker-library/php/issues/236)
    --enable-ftp \
    # --enable-mbstring is included here because otherwise there's no way to get pecl to use it properly (see https://github.com/docker-library/php/issues/195)
    --enable-mbstring \
    --enable-mysqlnd \
    --enable-opcache \
    # Allows to use the opcache.file_cache option
    --enable-opcache-file \
    --enable-soap \
    --enable-zip \
    --with-curl \
    --with-openssl \
    --with-zlib \
    --with-gd \
    --with-pdo-mysql \
    # https://github.com/docker-library/php/issues/439
    --with-mhash

RUN make -j 5
RUN make install

# Compile the intl extension because it cannot be installed with pecl
WORKDIR /php-src-$PHP_VERSION/ext/intl
RUN phpize
RUN ./configure
RUN make
RUN make install

WORKDIR /
