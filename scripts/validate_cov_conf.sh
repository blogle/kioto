#!/usr/bin/env bash

cat codecov.yml | curl --data-binary @- https://codecov.io/validate
