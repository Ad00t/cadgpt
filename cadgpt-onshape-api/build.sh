#!/bin/bash
set -e

DIRECTORY="$(pwd)"
LAMBDA_NAME="cadgpt-onshape-api"
BUCKET="cadgpt-lambdas"
LAYER_ZIP="${LAMBDA_NAME}_layer-python.zip"
LAMBDA_ZIP="${LAMBDA_NAME}_lambda.zip"
IMAGE_NAME="${LAMBDA_NAME}_layer-builder-py3.13-arm64"
BUILD_DIR="build"

rm -rf "${BUILD_DIR}" "${LAMBDA_ZIP}" layers
mkdir -p layers
mkdir -p "${BUILD_DIR}/python"

docker build --platform linux/arm64 -t "${IMAGE_NAME}" .
CONTAINER_ID=$(docker create --platform linux/arm64 "${IMAGE_NAME}")

docker cp "${CONTAINER_ID}:/opt/python/." "${BUILD_DIR}/python/"
docker rm -v "${CONTAINER_ID}"

cd "${BUILD_DIR}"
zip -r9 "../layers/${LAYER_ZIP}" .
cd ..
rm -rf "${BUILD_DIR}"

zip -r9 "${LAMBDA_ZIP}" "lambda_function.py" 

aws s3 cp "${LAMBDA_ZIP}" "s3://${BUCKET}/${LAMBDA_ZIP}"
aws s3 cp "layers/${LAYER_ZIP}" "s3://${BUCKET}/layers/${LAYER_ZIP}"
