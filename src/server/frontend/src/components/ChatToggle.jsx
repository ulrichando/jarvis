export default function ChatToggle({ onClick, isOpen }) {
  return (
    <div
      onClick={onClick}
      title="Open Chat (C)"
      className={`fixed bottom-5 right-5 w-[50px] h-[50px] bg-jarvis-cyan/15 border border-jarvis-bright/40 rounded-full flex items-center justify-center cursor-pointer z-[1000] text-xl text-jarvis-bright transition-all duration-300 hover:bg-jarvis-cyan/30 hover:scale-110 ${
        isOpen ? 'opacity-50' : 'opacity-100'
      }`}
      style={{ boxShadow: '0 0 15px rgba(0,229,255,0.2)' }}
    >
      <span>&#x25C8;</span>
    </div>
  )
}
