type WebSocketEventHandler = (data: any) => void;

class WebSocketService {
  private ws: WebSocket | null = null;
  private reconnectInterval = 3000;
  private maxReconnectInterval = 30000;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private listeners: Map<string, WebSocketEventHandler[]> = new Map();
  public isConnected = false;
  private shouldConnect = false;

  constructor() {
    // 延迟连接，等待认证完成
    // 只有在已登录时才尝试连接
    if (localStorage.getItem('auth_logged_in') === 'true') {
      this.shouldConnect = true;
      this.connect();
    }
  }

  public start() {
    // 手动启动 WebSocket 连接
    this.shouldConnect = true;
    if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
      this.connect();
    }
  }

  public stop() {
    // 停止 WebSocket 连接，并取消已排队的重连定时器
    this.shouldConnect = false;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private connect() {
    // stop() 之后再触发的定时回调不应建立连接
    if (!this.shouldConnect) {
      return;
    }

    // Determine the protocol (ws or wss) based on the current page protocol
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host; // This includes port if present

    const url = `${protocol}//${host}/ws`;

    console.log(`Connecting to WebSocket at ${url}`);
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log('WebSocket connected');
      this.isConnected = true;
      // 连接成功后重置退避间隔
      this.reconnectInterval = 3000;
      this.emit('connected', { isConnected: true });
    };

    this.ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        // Expecting message format: { type: 'event_type', data: ... }
        if (message.type) {
          this.emit(message.type, message.data);
        }
      } catch (e) {
        console.error('Failed to parse WebSocket message', e);
      }
    };

    this.ws.onclose = () => {
      if (this.isConnected) {
        console.log('WebSocket disconnected');
        this.isConnected = false;
        this.emit('disconnected', { isConnected: false });
      }
      // 只有在 shouldConnect 为 true 时才重连（登出后不再复活连接）
      if (this.shouldConnect) {
        this.reconnectTimer = setTimeout(() => this.connect(), this.reconnectInterval);
        // 指数退避，避免服务宕机时无限高频重试
        this.reconnectInterval = Math.min(this.reconnectInterval * 2, this.maxReconnectInterval);
      }
    };

    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      // Close will trigger onclose which handles reconnect
      this.ws?.close();
    };
  }

  public on(event: string, handler: WebSocketEventHandler) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event)?.push(handler);
  }

  public off(event: string, handler: WebSocketEventHandler) {
    const handlers = this.listeners.get(event);
    if (handlers) {
      const index = handlers.indexOf(handler);
      if (index !== -1) {
        handlers.splice(index, 1);
      }
    }
  }

  private emit(event: string, data: any) {
    const handlers = this.listeners.get(event);
    if (handlers) {
      handlers.forEach((handler) => handler(data));
    }
  }
}

// Export a singleton instance
export const wsService = new WebSocketService();
