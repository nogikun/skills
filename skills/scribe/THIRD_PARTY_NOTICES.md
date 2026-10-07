# 第三者ライセンス

確認日: 2026-10-08

Scribe本体のApache-2.0ライセンスは、モデルや依存ライブラリのライセンスを変更しません。本リポジトリはモデル重みや依存ライブラリのバイナリを同梱せず、セットアップ・実行時に各配布元から取得します。以下は主要構成要素の案内であり、モデルや依存ライブラリを同梱した製品の完全なライセンス表示を代替するものではありません。

## モデル

| 用途・モデル | 配布元・帰属先 | ライセンスと条件 |
|---|---|---|
| 話者分離: Nemotron 3 Diarization | [NVIDIA](https://huggingface.co/nvidia/Nemotron-3-Diarization) | [OpenMDW-1.1](https://openmdw.ai/license/1-1/)。商用利用可能。モデルの再配布時は規約全文と適用される著作権・出所表示を保持。規約はモデル出力の使用・変更・共有に制限を課していません。 |
| 文字起こし: Whisper small（CTranslate2形式） | [SYSTRAN配布モデル](https://huggingface.co/Systran/faster-whisper-small)、[OpenAI Whisper](https://github.com/openai/whisper) | [MIT](https://github.com/openai/whisper/blob/main/LICENSE)。商用利用可能。再配布時は著作権・ライセンス表示を保持。 |
| 音声区間検出: Silero VAD（faster-whisper同梱） | [Silero Team](https://github.com/snakers4/silero-vad) | [MIT](https://github.com/snakers4/silero-vad/blob/master/LICENSE)。商用利用可能。再配布時は著作権・ライセンス表示を保持。 |
| 話者分離: pyannote Community-1（任意） | [pyannote](https://huggingface.co/pyannote/speaker-diarization-community-1) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。商用利用可能。モデルの共有時は適切な帰属表示、ライセンスリンク、変更の表示が必要。取得には配布元の利用条件への同意とHugging Faceトークンが必要。 |
| sherpa用の分割: pyannote segmentation-3.0（ONNX形式） | [Fangjun Kuang配布モデル](https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0)、[元モデル](https://huggingface.co/pyannote/segmentation-3.0)、CNRS | [MIT](https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/blob/main/LICENSE)。商用利用可能。再配布時は著作権・ライセンス表示を保持。 |
| sherpa用の話者埋め込み: CAM++ Chinese-English（ONNX形式） | [Fangjun Kuang配布モデル](https://huggingface.co/csukuangfj/speaker-embedding-models)、[iic / 3D-Speakerの元モデル](https://modelscope.cn/models/iic/speech_campplus_sv_zh_en_16k-common_advanced) | 元モデルの公式カードはApache License 2.0を宣言しています。現在のONNX配布先にはLICENSE・モデルカードがないため、変換済み配布物の来歴と表示は再配布前に確認してください。 |

CAM++の既定ファイルは `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx` です。[sherpa-onnxの変換スクリプト](https://github.com/k2-fsa/sherpa-onnx/blob/master/scripts/3dspeaker/export-onnx.py) は同名のModelScopeモデルを取得してONNXに変換します。元モデルがApache-2.0であることと、現在取得するONNXファイルの来歴が完全に確認できていることは区別してください。

モデルはNemotron、pyannote、sherpaの順に切り替わるため、実際に使ったモデルの条件が適用されます。`--model` や `SCRIBE_SHERPA_EMBEDDING` で別モデルを指定する場合は、そのモデルのライセンスを別途確認してください。

コードが固定するモデルのリビジョンは次のとおりです。

| 配布モデル | リビジョン |
|---|---|
| `nvidia/Nemotron-3-Diarization` | `f667ed73aee57d40cc39428eb768b4fd87a0a29e` |
| `Systran/faster-whisper-small` | `536b0662742c02347bc0e980a01041f333bce120` |
| `pyannote/speaker-diarization-community-1` | `3533c8cf8e369892e6b79ff1bf80f7b0286a54ee` |
| `csukuangfj/sherpa-onnx-pyannote-segmentation-3-0` | `9403a6902bb58e3d5ae8c7e77c3422de279db2e0` |
| `csukuangfj/speaker-embedding-models` | `0743f301363dec56491a490f6d6cbc9d67f9a3bf` |

## 主要ライブラリ

| ライブラリ | ライセンス |
|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE)、[CTranslate2](https://github.com/OpenNMT/CTranslate2/blob/master/LICENSE)、[ONNX Runtime](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) | MIT |
| [Hugging Face Hub](https://github.com/huggingface/huggingface_hub/blob/main/LICENSE)、[Transformers](https://github.com/huggingface/transformers/blob/main/LICENSE)、[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE) | Apache-2.0 |
| [PyTorch](https://github.com/pytorch/pytorch/blob/main/LICENSE)、[NumPy](https://github.com/numpy/numpy/blob/main/LICENSE.txt)、[PyAV](https://github.com/PyAV-Org/PyAV/blob/main/LICENSE.txt) | BSD-3-Clause（同梱する第三者部品には別の条件が適用される場合があります） |
| [librosa](https://github.com/librosa/librosa/blob/main/LICENSE.md) | ISC |
| [pyannote.audio](https://github.com/pyannote/pyannote-audio/blob/develop/LICENSE) | MIT（モデルのCC BY 4.0とは別） |
| [tqdm](https://github.com/tqdm/tqdm/blob/master/LICENCE) | MPL-2.0およびMIT。対象ファイルごとに適用条件が異なります。 |

上記の主要ライブラリは商用利用を認めるライセンスで提供されています。再配布時は、間接依存や同梱バイナリを含めて、その配布物に付属するLICENSE・NOTICE・著作権表示を確認し、必要な表示とソース提供条件を満たしてください。

## FFmpegなどのバイナリを含む再配布

PyAVのソースコードのBSDライセンスだけで、同梱FFmpegの条件を判断することはできません。[FFmpegの公式説明](https://ffmpeg.org/legal.html)では、通常はLGPL、GPL対象の部品を組み込む場合はGPLが適用されます。

確認したWindows環境のPyAV 19.0.1では、同梱FFmpegの表示は `LGPL version 3 or later` でした。一方、ビルド設定ではx264/x265も有効になっています。表示だけから当該バイナリの再配布条件を確定せず、配布元のビルド・ライセンス情報まで確認してください。他OS・別バージョンのバイナリにはこの確認結果を適用できません。

Nemotron構成で使うlibrosaの間接依存にはLGPLのsoxrやlibsndfileがあり、GPU構成にはNVIDIA製の実行ライブラリも含まれます。実行環境、Dockerイメージ、インストーラーなどを配布する場合は、実際に含める各バイナリの条件を確認してください。

## 生成した文字起こし・字幕

Scribe本体のApache-2.0は、ツールで生成した文字起こし・字幕に自動的に適用されるものではありません。入力音声や動画の著作権、録音の同意、個人情報の取り扱いなどは、モデルやソフトウェアのライセンスとは別に扱ってください。
