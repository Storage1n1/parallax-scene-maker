import json
import requests
import sys

OLLAMA_URL = "http://localhost:11434/api/chat"
LLM_MODEL = "gemma4:e2b"

def generate_choreography(brief: str) -> dict:
    prompt = f"""
    Scene brief: "{brief}"
    
    You are a camera choreographer. 
    First, draw an ASCII map showing the positions of layers (BG, MG, FG) at keyframes (t=0 to t=3).
    Use this exact 2D Cartesian plane template for your ASCII map:
    
  9 |                                      
  8 |                                      
  7 |                                      
  6 |                                      
  5 |                                      
  4 |                                      
  3 |        |  C  | <--- (Camera)         
  2 |                                      
  1 |                                      
  0 +--------------------------------------------------
    0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 X-Axis
    
    Second, output the structured JSON data representing these 3D transform layers based on your map.
    
    Follow this JSON schema EXACTLY:
    {{
        "ascii_map": "Your multiline ASCII layout here...",
        "timeline": {{
            "shot_type": "dolly_pan_and_tilt",
            "layers": {{
                "BG": {{"depth_factor": 0.2, "start_x": 10, "start_y": 8, "end_x": 15, "end_y": 9}},
                "MG": {{"depth_factor": 0.5, "start_x": 7, "start_y": 5, "end_x": 22, "end_y": 4}},
                "FG": {{"depth_factor": 1.0, "start_x": 2, "start_y": 2, "end_x": 30, "end_y": 1}}
            }},
            "keyframe_duration_seconds": 3.0
        }}
    }}
    
    Respond ONLY with the JSON object.
    """
    
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    
    print(f"Generating choreography for: {brief}")
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=120)
        r.raise_for_status()
        content = r.json()["message"]["content"]
        
        # Extract JSON using regex in case the model writes markdown
        import re
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        return json.loads(content)
    except Exception as e:
        print(f"Error: {e}")
        return None

def generate_3d_transformations(data, screen_scale=10.0):
    transform_timeline = []
    duration = data.get("keyframe_duration_seconds", 3.0)
    
    for layer_name, metrics in data.get("layers", {}).items():
        start_x = metrics.get("start_x", 0) * (screen_scale * metrics.get("depth_factor", 1.0))
        end_x = metrics.get("end_x", 0) * (screen_scale * metrics.get("depth_factor", 1.0))
        
        start_y = metrics.get("start_y", 0) * (screen_scale * metrics.get("depth_factor", 1.0))
        end_y = metrics.get("end_y", 0) * (screen_scale * metrics.get("depth_factor", 1.0))
        
        transform_timeline.append({
            "layer": layer_name,
            "animation": "translate",
            "from_x": start_x,
            "to_x": end_x,
            "from_y": start_y,
            "to_y": end_y,
            "duration": f"{duration}s"
        })
        
    return transform_timeline

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python camera_choreographer.py 'brief text'")
        sys.exit(1)
        
    brief = sys.argv[1]
    result = generate_choreography(brief)
    
    if result:
        print("\n=== ASCII SCENE CARD ===")
        print(result.get("ascii_map", "No map generated."))
        
        print("\n=== PARSED 3D KEYFRAMES ===")
        transforms = generate_3d_transformations(result.get("timeline", {}))
        print(json.dumps(transforms, indent=4))
        
        with open("camera_timeline.json", "w") as f:
            json.dump({"raw": result, "transforms": transforms}, f, indent=4)
        print("\nSaved to camera_timeline.json")
