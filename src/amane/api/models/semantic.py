from pydantic import BaseModel


class SemanticModelCounts(BaseModel):
    """单个模型的向量条数, 按来源拆分."""

    count: int
    covers: int
    stills: int


class SemanticStatusResponse(BaseModel):
    """语义检索状态: 当前模型、各模型条数与后端; 不触发模型下载或加载."""

    enabled: bool
    backend: str | None = None
    current_model: str | None = None
    """当前使用的模型键 (``name-version``)."""
    current_model_label: str | None = None
    """当前模型的展示名, 大小写按厂商写法."""
    backend_label: str | None = None
    """推理后端展示名, 如 Core ML / ONNX Runtime."""
    text_loaded: bool = False
    """文本塔当前是否在内存中."""
    vision_loaded: bool = False
    """视觉塔当前是否在内存中."""
    search_ready: bool = False
    """文本塔与 tokenizer 已在本地就绪, 检索可用; 否则检索返回 503."""
    index_ready: bool = False
    """视觉塔已在本地就绪; 未就绪时索引任务会先下载模型."""
    download_size: int = 0
    """当前后端首次下载模型的总字节数; 已就绪时不再下载."""
    vision_size: int = 0
    """视觉塔模型文件解压后的大小."""
    text_size: int = 0
    """文本塔模型文件解压后的大小."""
    model_path: str = ""
    """模型目录路径, 用于界面展示."""
    model_bytes: int = 0
    """模型目录在本地占用的字节数."""
    embeddings_path: str = ""
    """向量库文件路径."""
    embeddings_bytes: int = 0
    """向量库及其 WAL 文件占用的字节数."""
    indexed_metadata: int = 0
    """当前模型下已建有向量的条目数."""
    indexed_at: str | None = None
    """当前模型最后一次写入向量的时间; 未建索引时为 null."""
    models: dict[str, SemanticModelCounts] = {}
    """库内已有向量的模型键 → 条数; 含切换前的旧模型."""
