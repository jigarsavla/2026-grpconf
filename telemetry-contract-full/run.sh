#!/usr/bin/env bash
# Telemetry contract lab -- reproduces everything in ~7 minutes.
set -e
cd "$(dirname "$0")"
pip install --break-system-packages -q grpcio==1.83.0 grpcio-tools==1.83.0 \
  grpcio-observability==1.83.0 opentelemetry-sdk==1.44.0 \
  opentelemetry-semantic-conventions==0.65b0 PyYAML || true
python3 -m grpc_tools.protoc -Iproto --python_out=. --grpc_python_out=. proto/inference.proto
mkdir -p out
{
  python3 -u exp_a_matrix.py
  python3 -u exp_b_arbitration.py
  python3 -u exp_c_contract.py
  python3 -u exp_d_stability.py
} 2>&1 | tee out/OUTPUT.txt
echo "wrote out/OUTPUT.txt"
