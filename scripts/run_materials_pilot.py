"""Offline CPU pilot. One model per process, frozen AI reference, resumable output."""
import argparse
import hashlib
import json
import os
import sys
import time
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'runtime/experiments/materials-20260928'
DATA = ROOT / 'data/experiments/materials-20260928'
sys.path.insert(0, str(WORK / 'packages'))
os.environ.update(OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', TOKENIZERS_PARALLELISM='false',
                  HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                  HF_HOME=str(WORK / 'hf-cache'), YOLO_CONFIG_DIR=str(WORK / 'yolo-settings'))

import psutil
from PIL import Image, ImageDraw, ImageOps


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=['hazard', 'sitesense', 'grounding-dino', 'grounding-materials'], required=True)
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / 'yolo-settings/Ultralytics').mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    process = psutil.Process()
    if os.name == 'nt':
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    reference_path = DATA / 'reference.json'
    reference = json.loads(reference_path.read_text(encoding='utf-8'))
    ref_hash = sha(reference_path)
    output_path = DATA / f'{args.model}.json'
    result = {'model': args.model, 'reference_sha256': ref_hash, 'device': 'cpu', 'threads': 2,
              'results': [], 'complete': False}
    if output_path.exists():
        result = json.loads(output_path.read_text(encoding='utf-8'))
        if result['reference_sha256'] != ref_hash:
            raise ValueError('Reference changed; use a new experiment directory')
    def save():
        temp = output_path.with_suffix('.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(output_path)
    if psutil.virtual_memory().available < 3 * 2**30:
        raise RuntimeError('Less than 3 GiB free RAM: postpone this CPU run')
    import torch
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    result['torch_version'] = torch.__version__
    peak = [process.memory_info().rss]
    stop = threading.Event()
    def sample_memory():
        while not stop.wait(0.1):
            peak[0] = max(peak[0], process.memory_info().rss)
    monitor = threading.Thread(target=sample_memory, daemon=True)
    monitor.start()
    start = time.perf_counter()
    if args.model.startswith('grounding-'):
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        import transformers
        model_dir = WORK / 'models/grounding-dino'
        processor = AutoProcessor.from_pretrained(str(model_dir), local_files_only=True)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(str(model_dir), local_files_only=True)
        model.config.disable_custom_kernels = True
        model.eval()
        result['weights_sha256'] = sha(model_dir / 'model.safetensors')
        result['transformers_version'] = transformers.__version__
        prompt = ('rebar. brick wall. steel pipe. formwork.' if args.model == 'grounding-materials'
                  else reference['grounding_dino_prompt'])
        result['parameters'] = {'shortest_edge':512,'longest_edge':768,'box_threshold':0.3,
                                'text_threshold':0.25,'prompt':prompt}
        if args.model == 'grounding-materials':
            result['study_note'] = 'Exploratory prompt ablation chosen AFTER primary inference; not independent validation'
        def predict(im):
            inputs = processor(images=im, text=prompt, return_tensors='pt',
                               size={'shortest_edge':512,'longest_edge':768})
            with torch.inference_mode():
                outputs = model(**inputs)
            import inspect
            keyword = 'threshold' if 'threshold' in inspect.signature(processor.post_process_grounded_object_detection).parameters else 'box_threshold'
            detections = processor.post_process_grounded_object_detection(
                outputs, inputs.input_ids, **{keyword:0.3}, text_threshold=0.25,
                target_sizes=[im.size[::-1]])[0]
            labels = detections.get('text_labels', detections.get('labels'))
            return [{'label':str(label),'confidence':float(score),'bbox_xyxy':box.tolist()}
                    for label, score, box in zip(labels,detections['scores'],detections['boxes'])]
    else:
        from ultralytics import YOLO
        import ultralytics
        path = (ROOT / 'runtime/models/construction-hazard/yolo11n.pt' if args.model == 'hazard'
                else WORK / 'models/sitesense/yolo26l_construction_v1.pt')
        model = YOLO(str(path))
        result['weights_sha256'] = sha(path)
        result['ultralytics_version'] = ultralytics.__version__
        result['classes'] = model.names
        result['parameters'] = {'imgsz':640,'confidence':0.25,'iou':0.7,'max_det':300}
        def predict(im):
            output = model.predict(im, device='cpu', imgsz=640, conf=0.25, iou=0.7,
                                   max_det=300, verbose=False)[0]
            return [{'label':output.names[int(b.cls.item())], 'confidence':float(b.conf.item()),
                     'bbox_xyxy':b.xyxy[0].tolist()} for b in output.boxes]
    result['last_load_seconds'] = time.perf_counter() - start
    print(f"Loaded {args.model} in {result['last_load_seconds']:.1f}s", flush=True)
    done = {r['image'] for r in result['results']}
    selected = reference['images'][:args.limit or None]
    overlay_dir = WORK / 'overlays' / args.model
    overlay_dir.mkdir(parents=True, exist_ok=True)
    for row in selected:
        if row['image'] in done:
            continue
        if psutil.virtual_memory().available < 1.5 * 2**30:
            result['stopped_reason'] = 'less than 1.5 GiB available RAM'
            save()
            break
        path = ROOT / row['image']
        if sha(path) != row['sha256']:
            raise ValueError(f'Input changed: {path}')
        with Image.open(path) as source:
            im = source.convert('RGB')
        started = time.perf_counter()
        detections = predict(im)
        elapsed = time.perf_counter() - started
        result['results'].append({'image':row['image'],'size':im.size,'seconds':elapsed,
                                  'detections':detections})
        result['peak_process_rss_gib'] = round(peak[0]/2**30,3)
        save()
        canvas = ImageOps.contain(im, (1200,900))
        draw = ImageDraw.Draw(canvas)
        sx,sy = canvas.width/im.width,canvas.height/im.height
        for detection in detections:
            x1,y1,x2,y2 = detection['bbox_xyxy']
            box = (x1*sx,y1*sy,x2*sx,y2*sy)
            draw.rectangle(box,outline='#ff3030',width=2)
            draw.text((box[0],max(0,box[1]-12)),f"{detection['label']} {detection['confidence']:.2f}",fill='#ff3030',stroke_width=1,stroke_fill='white')
        canvas.save(overlay_dir / f'{path.stem}.jpg')
        print(f'{path.name}: {elapsed:.2f}s, {len(detections)} detections, peak RSS {peak[0]/2**30:.2f} GiB',flush=True)
        time.sleep(0.2)
    stop.set()
    result['peak_process_rss_gib'] = max(result.get('peak_process_rss_gib',0),round(peak[0]/2**30,3))
    result['complete'] = len(result['results']) == len(reference['images'])
    save()


if __name__ == '__main__':
    main()
