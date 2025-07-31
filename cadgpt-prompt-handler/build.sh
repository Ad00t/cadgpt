#!/bin/bash
set -e

DIRECTORY="$(pwd)"
LAMBDA_NAME="cadgpt-prompt-handler"
BUCKET="cadgpt-lambdas"
LAYER_ZIP="${LAMBDA_NAME}_layer-python.zip"
LAMBDA_ZIP="${LAMBDA_NAME}_lambda.zip"
IMAGE_NAME="${LAMBDA_NAME}_layer-builder-py3.13-arm64"
BUILD_DIR="build"

rm -rf "${BUILD_DIR}" "${LAMBDA_ZIP}" layers
mkdir -p layers
mkdir -p "${BUILD_DIR}/python"

docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
docker buildx build --platform linux/arm64 --load -t "${IMAGE_NAME}" .
CONTAINER_ID=$(docker create --platform linux/arm64 "${IMAGE_NAME}")

docker cp "${CONTAINER_ID}:/opt/python/." "${BUILD_DIR}/python/"
docker rm -v "${CONTAINER_ID}"

cd "${BUILD_DIR}"
zip -r9 "../layers/${LAYER_ZIP}" .
cd ..
rm -rf "${BUILD_DIR}"

zip -r9 "${LAMBDA_ZIP}" "llm_static/features_api_doc.txt" "llm_static/llm_instructions.txt" "llm_static/final_schema.json" "global-bundle.pem" "lambda_function.py" 

aws s3 cp "${LAMBDA_ZIP}" "s3://${BUCKET}/${LAMBDA_ZIP}"
aws s3 cp "layers/${LAYER_ZIP}" "s3://${BUCKET}/layers/${LAYER_ZIP}"
