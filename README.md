PHP was always already serverless
=================================

This is a prototype for running PHP in AWS Lambda basically as fast as one
of the natively supported languages.

Installation
------------

Make a pyenv for Python 3.6. AWS Lambda currently uses Python 3.6.1:

```bash
pyenv install 3.6.1
pyenv local 3.6.1
```

Then use this pyenv for a new pipenv:

```bash
pipenv install --python $(pyenv which python)
```

Also you need to build PHP-FPM on Amazon Linux 2017.03

```bash
cd php-fpm
./build.sh 7.2.11
```

You'll need to set up the Lambda function and API Gateway resources. There is some terraform in the `terraform`
directory to get you started, but you'll need to modify it to suit your needs.

Finally you can put PHP code in the `php` directory. And run these commands to deploy:

```bash
pip install --requirement requirements.txt --target build
cp -R php-fpm app.py fcgi_client.py php build
cd build && zip -r build.zip * && aws s3 cp build.zip s3://[deployment bucket]/[deployment key]
aws lambda update-function-code --function-name php-lambda --s3-bucket [deployment bucket] --s3-key [deployment key]
```

Usage
-----

You should be able to hit these routes without any additional code:

* /phpinfo.php
* /ping
* /status (and /status?full&html)


Credits
=======

This project uses code from other open source projects:

* https://www.saddi.com/software/flup/
* https://github.com/agronholm/fcgiproto/
* https://github.com/mnapoli/bref
