const KEY='my-legend:login-return'
let redirecting=false
export function redirectToLogin(){
  if(redirecting)return
  rememberDestination();redirecting=true
  uni.reLaunch({url:'/pages/login/index',fail:()=>{},complete:()=>{setTimeout(()=>{redirecting=false},0)}})
}
export function safeDestination(value:unknown):value is string {
  return typeof value==='string'&&/^\/pages\/[a-z-]+\/index(?:\?[^#\r\n]*)?$/.test(value)&&!value.startsWith('/pages/login/')
}
export function rememberDestination(){
  if(typeof window==='undefined')return
  const url=window.location.hash.slice(1)
  if(safeDestination(url))uni.setStorageSync(KEY,url)
}
export function returnAfterLogin(){
  const target=uni.getStorageSync(KEY);uni.removeStorageSync(KEY)
  uni.reLaunch({url:safeDestination(target)?target:'/pages/home/index'})
}
