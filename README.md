# loudtqdm

tqdm but it's loud.

## Install

```bash
pip install loudtqdm
```

## Usage

```python
import loudtqdm as tqdm  # or `from loudtqdm import loudtqdm as tqdm`

for item in tqdm(my_list, desc="LOADING"):
    process(item)
```

## Requirements

- Python 3.10+
- macOS (CoreAudio) or Linux (aplay)
- No pip dependencies
