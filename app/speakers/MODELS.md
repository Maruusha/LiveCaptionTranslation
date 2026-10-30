# Local speaker models

Live speaker labels use CPU-only `sherpa-onnx 1.12.33` with TitaNet
embeddings (one CPU thread). Each completed VAD chunk receives one label;
this live path does not yet split speaker changes inside a chunk or detect
overlap. The segmentation model below is used by the separate offline
diarization diagnostic (two CPU threads).

Ignored files under `models/speakers/`:

- `sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx`
  - Source: `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2`
  - SHA-256: `D582F4B4C6B48205DE7E0643C57DF0DF5615A3C176189BE3FC461E9D18827B5D`
- `nemo_en_titanet_small.onnx`
  - Source: `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx`
  - SHA-256: `AD4A1802485D8B34C722D2A9D04249662F2ECE5D28A7A039063CA22F515A789E`
- `evaluation/1-two-speakers-en.wav`
  - Public sherpa-onnx evaluation sample
  - SHA-256: `F1C877DC01595E28BE7147BF2FE38E5268147A868BF3FDB5C37B97F5940E21F3`

Raw user audio is never written here. Repeat the offline sample check with
`.venv\Scripts\python.exe -m app.diagnostics.speaker_evaluation`.
